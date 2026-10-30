//! File operations never overwrite an existing destination. Unix traversal uses
//! directory descriptors and O_NOFOLLOW, including every parent component.
use crate::model::*;
use serde::Serialize;
use sha2::{Digest, Sha256};
use std::{
    fs::{self, File, OpenOptions},
    io::{Read, Write},
    path::{Component, Path, PathBuf},
    time::UNIX_EPOCH,
};

pub fn fingerprint(m: &fs::Metadata) -> Fingerprint {
    #[cfg(unix)]
    let identity = {
        use std::os::unix::fs::MetadataExt;
        format!("{}:{}", m.dev(), m.ino())
    };
    #[cfg(windows)]
    let identity = {
        // Windows 上没有稳定的「卷序列号 + 文件索引」接口：
        // `MetadataExt::volume_serial_number()/file_index()` 仍属不稳定特性
        // `windows_by_handle`，直接用会让 Windows 目标编译失败。
        // 这里退化为「创建时间 + 最后写时间 + 属性 + 大小」组合，仍能区分
        // 「换了一块盘 / 换了一个文件」；真正的差异检测还有 bytes + modified_ns，
        // 以及每次读写前的重新指纹比对，不单靠 identity。
        use std::os::windows::fs::MetadataExt;
        format!(
            "{:?}:{:?}:{:?}:{}",
            m.creation_time(),
            m.last_write_time(),
            m.file_attributes(),
            m.len()
        )
    };
    #[cfg(not(any(unix, windows)))]
    let identity = format!("{:?}", m.created().ok());
    Fingerprint {
        bytes: m.len(),
        modified_ns: m
            .modified()
            .ok()
            .and_then(|t| t.duration_since(UNIX_EPOCH).ok())
            .map_or(0, |d| d.as_nanos()),
        identity,
    }
}
pub fn relative(path: &Path) -> CoreResult<()> {
    if path.as_os_str().is_empty()
        || path
            .components()
            .any(|c| !matches!(c, Component::Normal(_)))
    {
        return Err(CoreError::new(
            "UNSAFE_PATH",
            "只允许非空的相对路径，不能包含 .. 或绝对路径",
        ));
    }
    if path
        .components()
        .any(|c| c.as_os_str().to_string_lossy().contains(['\0', ':', '\\']))
    {
        return Err(CoreError::new("UNSAFE_PATH", "文件名包含不可移植字符"));
    }
    Ok(())
}
pub fn local_directory(path: &Path) -> CoreResult<PathBuf> {
    // canonicalize() 在 Windows 上返回 \\?\C:\...，而 select_directory 会把
    // canonical 路径回传给前端、再传回来。verbatim 的盘符路径同样是本机路径，
    // 只有 UNC 共享和设备命名空间必须拒绝。
    #[cfg(windows)]
    let local_prefix = matches!(
        path.components().next(),
        Some(Component::Prefix(prefix))
            if matches!(prefix.kind(), std::path::Prefix::Disk(_) | std::path::Prefix::VerbatimDisk(_))
    );
    #[cfg(not(windows))]
    let local_prefix =
        !path.to_string_lossy().starts_with("//") && !path.to_string_lossy().starts_with("\\\\");
    if !path.is_absolute() || !local_prefix {
        return Err(CoreError::new(
            "LOCAL_PATH_REQUIRED",
            "请选择本机/移动磁盘的绝对路径，不支持网络共享",
        ));
    }
    if path.components().any(|c| matches!(c, Component::ParentDir)) {
        return Err(CoreError::new("UNSAFE_PATH", "路径包含上级目录"));
    }
    let mut walked = PathBuf::new();
    for c in path.components() {
        walked.push(c.as_os_str());
        // \\?\D: 这种前缀本身不是文件系统条目，补上根分隔符之前不能查询，
        // 否则会得到 ERROR_INVALID_FUNCTION。
        if matches!(c, Component::Prefix(_)) {
            continue;
        }
        let m = fs::symlink_metadata(&walked)?;
        if m.file_type().is_symlink() {
            return Err(CoreError::new("SYMLINK_REJECTED", walked.display()));
        }
    }
    let canonical = path.canonicalize()?;
    if !canonical.is_dir() {
        return Err(CoreError::new("NOT_DIRECTORY", canonical.display()));
    }
    #[cfg(target_os = "macos")]
    {
        use std::ffi::{CStr, CString};
        use std::os::unix::ffi::OsStrExt;
        let c = CString::new(canonical.as_os_str().as_bytes())
            .map_err(|e| CoreError::new("UNSAFE_PATH", e))?;
        let mut st = std::mem::MaybeUninit::<libc::statfs>::uninit();
        if unsafe { libc::statfs(c.as_ptr(), st.as_mut_ptr()) } != 0 {
            return Err(std::io::Error::last_os_error().into());
        }
        let st = unsafe { st.assume_init() };
        let kind = unsafe { CStr::from_ptr(st.f_fstypename.as_ptr()) }.to_string_lossy();
        if ["smbfs", "nfs", "webdav", "afpfs"].contains(&kind.as_ref()) {
            return Err(CoreError::new("NETWORK_TARGET_UNSUPPORTED", kind));
        }
    }
    #[cfg(windows)]
    {
        use std::os::windows::ffi::OsStrExt;
        unsafe extern "system" {
            fn GetDriveTypeW(root: *const u16) -> u32;
        }
        let root = canonical.components().take(2).collect::<PathBuf>();
        let wide: Vec<u16> = root.as_os_str().encode_wide().chain(Some(0)).collect();
        if unsafe { GetDriveTypeW(wide.as_ptr()) } == 4 {
            return Err(CoreError::new(
                "NETWORK_TARGET_UNSUPPORTED",
                "网络映射盘不支持",
            ));
        }
    }
    Ok(canonical)
}
pub fn kind(path: &Path) -> String {
    let ext = path
        .extension()
        .unwrap_or_default()
        .to_string_lossy()
        .to_lowercase();
    if [
        "jpg", "jpeg", "png", "webp", "bmp", "heic", "heif", "tif", "tiff", "dng", "arw", "cr2",
        "cr3", "nef", "raf", "rw2", "orf", "pef",
    ]
    .contains(&ext.as_str())
    {
        "photo"
    } else if ["mp4", "mov", "mkv", "m4v", "avi", "mts", "m2ts", "webm"].contains(&ext.as_str()) {
        "video"
    } else {
        "other"
    }
    .into()
}

pub struct SafeDir {
    pub path: PathBuf,
    handle: File,
    identity: String,
}
impl SafeDir {
    pub fn open(path: &Path) -> CoreResult<Self> {
        let path = local_directory(path)?;
        #[cfg(unix)]
        let handle = {
            use std::os::unix::fs::OpenOptionsExt;
            OpenOptions::new()
                .read(true)
                .custom_flags(libc::O_DIRECTORY | libc::O_NOFOLLOW | libc::O_CLOEXEC)
                .open(&path)?
        };
        #[cfg(not(unix))]
        let handle = {
            use std::os::windows::fs::OpenOptionsExt;
            OpenOptions::new()
                .read(true)
                .custom_flags(0x02000000 | 0x00200000)
                .open(&path)?
        };
        let identity = fingerprint(&handle.metadata()?).identity;
        Ok(Self {
            path,
            handle,
            identity,
        })
    }
    pub fn check_root(&self) -> CoreResult<()> {
        let p = local_directory(&self.path)?;
        if fingerprint(&fs::metadata(p)?).identity != self.identity {
            return Err(CoreError::new("ROOT_CHANGED", "来源或目标磁盘/目录已变化"));
        }
        Ok(())
    }
    #[cfg(unix)]
    fn parent(&self, path: &Path, create: bool) -> CoreResult<(File, std::ffi::CString)> {
        use std::os::fd::{AsRawFd, FromRawFd};
        use std::os::unix::ffi::OsStrExt;
        relative(path)?;
        self.check_root()?;
        let mut dir = self.handle.try_clone()?;
        let parts: Vec<_> = path.components().collect();
        for c in &parts[..parts.len() - 1] {
            let name = std::ffi::CString::new(c.as_os_str().as_bytes())
                .map_err(|e| CoreError::new("UNSAFE_PATH", e))?;
            let mut fd = unsafe {
                libc::openat(
                    dir.as_raw_fd(),
                    name.as_ptr(),
                    libc::O_RDONLY | libc::O_DIRECTORY | libc::O_NOFOLLOW | libc::O_CLOEXEC,
                )
            };
            if fd < 0
                && create
                && std::io::Error::last_os_error().kind() == std::io::ErrorKind::NotFound
            {
                let code = unsafe { libc::mkdirat(dir.as_raw_fd(), name.as_ptr(), 0o700) };
                if code != 0
                    && std::io::Error::last_os_error().kind() != std::io::ErrorKind::AlreadyExists
                {
                    return Err(std::io::Error::last_os_error().into());
                }
                fd = unsafe {
                    libc::openat(
                        dir.as_raw_fd(),
                        name.as_ptr(),
                        libc::O_RDONLY | libc::O_DIRECTORY | libc::O_NOFOLLOW | libc::O_CLOEXEC,
                    )
                };
            }
            if fd < 0 {
                return Err(CoreError::new(
                    "UNSAFE_OR_MISSING_PARENT",
                    std::io::Error::last_os_error(),
                ));
            }
            dir = unsafe { File::from_raw_fd(fd) };
        }
        Ok((
            dir,
            std::ffi::CString::new(parts.last().unwrap().as_os_str().as_bytes())
                .map_err(|e| CoreError::new("UNSAFE_PATH", e))?,
        ))
    }
    #[cfg(not(unix))]
    fn checked_path(&self, path: &Path, create: bool) -> CoreResult<PathBuf> {
        relative(path)?;
        self.check_root()?;
        let mut p = self.path.clone();
        let parts: Vec<_> = path.components().collect();
        for c in &parts[..parts.len() - 1] {
            p.push(c);
            if create && !p.exists() {
                fs::create_dir(&p)?;
            }
            if fs::symlink_metadata(&p)?.file_type().is_symlink() {
                return Err(CoreError::new("SYMLINK_REJECTED", p.display()));
            }
        }
        p.push(parts.last().unwrap());
        if let Ok(m) = fs::symlink_metadata(&p) {
            if m.file_type().is_symlink() {
                return Err(CoreError::new("SYMLINK_REJECTED", p.display()));
            }
        }
        Ok(p)
    }
    pub fn read(&self, path: &Path) -> CoreResult<File> {
        #[cfg(unix)]
        let f = {
            use std::os::fd::{AsRawFd, FromRawFd};
            let (p, n) = self.parent(path, false)?;
            let fd = unsafe {
                libc::openat(
                    p.as_raw_fd(),
                    n.as_ptr(),
                    libc::O_RDONLY | libc::O_NOFOLLOW | libc::O_CLOEXEC | libc::O_NONBLOCK,
                )
            };
            if fd < 0 {
                return Err(std::io::Error::last_os_error().into());
            }
            unsafe { File::from_raw_fd(fd) }
        };
        #[cfg(not(unix))]
        let f = File::open(self.checked_path(path, false)?)?;
        if !f.metadata()?.is_file() {
            return Err(CoreError::new("NOT_REGULAR_FILE", path.display()));
        }
        Ok(f)
    }
    pub fn create(&self, path: &Path) -> CoreResult<File> {
        #[cfg(unix)]
        {
            use std::os::fd::{AsRawFd, FromRawFd};
            let (p, n) = self.parent(path, true)?;
            let fd = unsafe {
                libc::openat(
                    p.as_raw_fd(),
                    n.as_ptr(),
                    libc::O_WRONLY
                        | libc::O_CREAT
                        | libc::O_EXCL
                        | libc::O_NOFOLLOW
                        | libc::O_CLOEXEC,
                    0o600,
                )
            };
            if fd < 0 {
                return Err(std::io::Error::last_os_error().into());
            }
            Ok(unsafe { File::from_raw_fd(fd) })
        }
        #[cfg(not(unix))]
        {
            Ok(OpenOptions::new()
                .write(true)
                .create_new(true)
                .open(self.checked_path(path, true)?)?)
        }
    }
    /// Atomic no-replace rename works on exFAT as well as hard-link capable disks.
    pub fn commit(&self, from: &Path, to: &Path) -> CoreResult<()> {
        #[cfg(unix)]
        {
            use std::os::fd::AsRawFd;
            let (a, x) = self.parent(from, false)?;
            let (b, y) = self.parent(to, true)?;
            #[cfg(target_os = "macos")]
            let rc = unsafe {
                libc::renameatx_np(
                    a.as_raw_fd(),
                    x.as_ptr(),
                    b.as_raw_fd(),
                    y.as_ptr(),
                    libc::RENAME_EXCL,
                )
            };
            #[cfg(target_os = "linux")]
            let rc = unsafe {
                libc::renameat2(
                    a.as_raw_fd(),
                    x.as_ptr(),
                    b.as_raw_fd(),
                    y.as_ptr(),
                    libc::RENAME_NOREPLACE,
                )
            };
            #[cfg(not(any(target_os = "macos", target_os = "linux")))]
            let rc = -1;
            if rc != 0 {
                return Err(std::io::Error::last_os_error().into());
            }
            a.sync_all()?;
            b.sync_all()?;
            Ok(())
        }
        #[cfg(windows)]
        {
            use std::os::windows::ffi::OsStrExt;
            unsafe extern "system" {
                fn MoveFileW(a: *const u16, b: *const u16) -> i32;
            }
            let a = self.checked_path(from, false)?;
            let b = self.checked_path(to, true)?;
            let a: Vec<_> = a.as_os_str().encode_wide().chain(Some(0)).collect();
            let b: Vec<_> = b.as_os_str().encode_wide().chain(Some(0)).collect();
            if unsafe { MoveFileW(a.as_ptr(), b.as_ptr()) } == 0 {
                return Err(std::io::Error::last_os_error().into());
            }
            Ok(())
        }
    }
    pub fn cleanup_temp(&self, path: &Path, identity: &str) {
        if self
            .read(path)
            .ok()
            .and_then(|f| f.metadata().ok())
            .is_none_or(|m| fingerprint(&m).identity != identity)
        {
            return;
        }
        #[cfg(unix)]
        {
            use std::os::fd::AsRawFd;
            if let Ok((p, n)) = self.parent(path, false) {
                unsafe { libc::unlinkat(p.as_raw_fd(), n.as_ptr(), 0) };
            }
        }
        #[cfg(not(unix))]
        {
            if let Ok(p) = self.checked_path(path, false) {
                let _ = fs::remove_file(p);
            }
        }
    }
}

pub fn hash(file: &mut File, check: &mut impl FnMut() -> CoreResult<()>) -> CoreResult<String> {
    let mut hasher = Sha256::new();
    let mut buf = vec![0; 1024 * 1024];
    loop {
        check()?;
        let n = file.read(&mut buf)?;
        if n == 0 {
            break;
        }
        hasher.update(&buf[..n]);
    }
    Ok(format!("{:x}", hasher.finalize()))
}
pub fn hash_at(
    root: &SafeDir,
    path: &Path,
    check: &mut impl FnMut() -> CoreResult<()>,
) -> CoreResult<String> {
    let mut f = root.read(path)?;
    let before = fingerprint(&f.metadata()?);
    let value = hash(&mut f, check)?;
    if before != fingerprint(&f.metadata()?) || before != fingerprint(&root.read(path)?.metadata()?)
    {
        return Err(CoreError::new("FILE_CHANGED", path.display()));
    }
    Ok(value)
}

/// 扫描时被跳过的条目。**不中断整体扫描**，逐条报告给调用方。
#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SkippedEntry {
    /// 相对路径；可能是不可移植的名字（含 `:` 等），所以用字符串而不是 PathBuf
    pub relative: String,
    /// `unportable_name` / `symlink` / `unreadable`
    pub reason: &'static str,
    /// 给人看的补充说明，例如具体哪个字符、或底层错误
    pub detail: Option<String>,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ScanReport {
    pub files: Vec<ScannedFile>,
    pub skipped: Vec<SkippedEntry>,
}

/// 名字里出现不可移植字符（`\0`、`:`、`\`）时，这个文件只在当前系统可用。
/// 我们**不再整批拒绝**：跳过一个文件，比让用户 3 万张照片一张都扫不进去要好。
fn unportable_reason(name: &str) -> Option<&'static str> {
    if name.contains('\0') {
        Some("包含 NUL 字符")
    } else if name.contains(':') {
        Some("包含冒号（在 Windows 上无法创建同名文件）")
    } else if name.contains('\\') {
        Some("包含反斜杠（会被不同系统当作路径分隔符）")
    } else {
        None
    }
}

/// 带跳过清单的扫描。目录里出现 `:` 文件名、符号链接、无权限子目录时，
/// 只跳过该条目并在 `skipped` 里说明原因。
pub fn scan_report(root: &Path) -> CoreResult<ScanReport> {
    let dir = SafeDir::open(root)?;
    let mut files = vec![];
    let mut skipped: Vec<SkippedEntry> = vec![];
    let iter = walkdir::WalkDir::new(&dir.path)
        .follow_links(false)
        .sort_by_file_name()
        .into_iter()
        .filter_entry(|e| {
            let n = e.file_name().to_string_lossy();
            n != ".yingxu-transfer-recovery"
                && !n.starts_with(".yingxu-part-")
                && !n.starts_with(".yingxu-probe-")
        });
    for entry in iter {
        let e = match entry {
            Ok(e) => e,
            // 单个子目录不可读（权限、坏扇区）不该让整次扫描失败
            Err(err) => {
                let path = err
                    .path()
                    .map(|p| {
                        p.strip_prefix(&dir.path)
                            .unwrap_or(p)
                            .to_string_lossy()
                            .to_string()
                    })
                    .unwrap_or_default();
                skipped.push(SkippedEntry {
                    relative: path,
                    reason: "unreadable",
                    detail: Some(err.to_string()),
                });
                continue;
            }
        };
        if e.depth() == 0 {
            continue;
        }
        let shown = e
            .path()
            .strip_prefix(&dir.path)
            .unwrap_or(e.path())
            .to_string_lossy()
            .to_string();
        // 不跟随符号链接：链接目标可能在来源目录之外，跟进去等于越界读
        if e.file_type().is_symlink() {
            skipped.push(SkippedEntry {
                relative: shown,
                reason: "symlink",
                detail: Some("不跟随符号链接".into()),
            });
            continue;
        }
        if e.file_type().is_dir() {
            continue;
        }
        if let Some(detail) = unportable_reason(&shown) {
            skipped.push(SkippedEntry {
                relative: shown,
                reason: "unportable_name",
                detail: Some(detail.into()),
            });
            continue;
        }
        let rel = match e.path().strip_prefix(&dir.path) {
            Ok(rel) => rel.to_path_buf(),
            Err(err) => {
                skipped.push(SkippedEntry {
                    relative: shown,
                    reason: "unreadable",
                    detail: Some(err.to_string()),
                });
                continue;
            }
        };
        let f = match dir.read(&rel) {
            Ok(f) => f,
            Err(err) => {
                skipped.push(SkippedEntry {
                    relative: rel.to_string_lossy().to_string(),
                    reason: "unreadable",
                    detail: Some(format!("{}: {}", err.code, err.message)),
                });
                continue;
            }
        };
        let meta = match f.metadata() {
            Ok(m) => m,
            Err(err) => {
                skipped.push(SkippedEntry {
                    relative: rel.to_string_lossy().to_string(),
                    reason: "unreadable",
                    detail: Some(err.to_string()),
                });
                continue;
            }
        };
        files.push(ScannedFile {
            kind: kind(&rel),
            relative: rel,
            fingerprint: fingerprint(&meta),
            expected_sha256: None,
        });
    }
    skipped.sort_by(|a, b| a.relative.cmp(&b.relative));
    Ok(ScanReport { files, skipped })
}

/// 兼容旧调用：只要文件列表。要拿到跳过清单请用 [`scan_report`]。
pub fn scan(root: &Path) -> CoreResult<Vec<ScannedFile>> {
    Ok(scan_report(root)?.files)
}
fn numbered(path: &Path, n: usize) -> PathBuf {
    if n == 0 {
        return path.into();
    }
    let stem = path.file_stem().unwrap_or_default().to_string_lossy();
    let ext = path
        .extension()
        .map(|s| format!(".{}", s.to_string_lossy()))
        .unwrap_or_default();
    path.with_file_name(format!("{stem} ({n}){ext}"))
}

pub struct CopySpec<'a> {
    pub source: &'a SafeDir,
    pub relative: &'a Path,
    pub expected: &'a Fingerprint,
    pub target: &'a SafeDir,
    pub output: &'a Path,
    pub verify: bool,
    pub expected_digest: Option<&'a str>,
}
pub struct CopyProgress {
    pub bytes: u64,
    pub total: u64,
    pub phase: &'static str,
}
#[allow(clippy::too_many_arguments)]
pub fn copy_checked(
    source: &SafeDir,
    relative: &Path,
    expected: &Fingerprint,
    target: &SafeDir,
    output: &Path,
    verify: bool,
    expected_digest: Option<&str>,
    check: &mut impl FnMut() -> CoreResult<()>,
) -> CoreResult<CopyResult> {
    copy_observed(
        CopySpec {
            source,
            relative,
            expected,
            target,
            output,
            verify,
            expected_digest,
        },
        check,
        &mut |_, _| Ok(()),
        &mut |_| Ok(()),
    )
}
pub fn copy_observed(
    spec: CopySpec<'_>,
    check: &mut impl FnMut() -> CoreResult<()>,
    journal: &mut impl FnMut(&Path, &Fingerprint) -> CoreResult<()>,
    progress: &mut impl FnMut(CopyProgress) -> CoreResult<()>,
) -> CoreResult<CopyResult> {
    let CopySpec {
        source,
        relative,
        expected,
        target,
        output,
        verify,
        expected_digest,
    } = spec;
    check()?;
    let mut input = source.read(relative)?;
    if fingerprint(&input.metadata()?) != *expected {
        return Err(CoreError::new("SOURCE_CHANGED", relative.display()));
    }
    let temp = output.with_file_name(format!(".yingxu-part-{}", id()));
    let mut out = target.create(&temp)?;
    let temp_identity = fingerprint(&out.metadata()?).identity;
    let result = (|| {
        journal(&temp, &fingerprint(&out.metadata()?))?;
        progress(CopyProgress {
            bytes: 0,
            total: expected.bytes,
            phase: "copying",
        })?;
        let mut copied = 0u64;
        let mut h = Sha256::new();
        let mut buf = vec![0; 1024 * 1024];
        loop {
            check()?;
            source.check_root()?;
            target.check_root()?;
            let n = input.read(&mut buf)?;
            if n == 0 {
                break;
            }
            out.write_all(&buf[..n])?;
            h.update(&buf[..n]);
            copied += n as u64;
            progress(CopyProgress {
                bytes: copied,
                total: expected.bytes,
                phase: "copying",
            })?;
        }
        out.sync_all()?;
        drop(out);
        if fingerprint(&input.metadata()?) != *expected
            || fingerprint(&source.read(relative)?.metadata()?) != *expected
        {
            return Err(CoreError::new("SOURCE_CHANGED", relative.display()));
        }
        let digest = format!("{:x}", h.finalize());
        if expected_digest.is_some_and(|expected| expected != digest) {
            return Err(CoreError::new(
                "EXPECTED_CHECKSUM_MISMATCH",
                relative.display(),
            ));
        }
        progress(CopyProgress {
            bytes: copied,
            total: expected.bytes,
            phase: "verifying",
        })?;
        if verify && hash_at(target, &temp, check)? != digest {
            return Err(CoreError::new("CHECKSUM_MISMATCH", output.display()));
        }
        for n in 0..10000 {
            check()?;
            let candidate = numbered(output, n);
            match target.read(&candidate) {
                Ok(mut existing) => {
                    let before = fingerprint(&existing.metadata()?);
                    let same = hash(&mut existing, check)? == digest;
                    if before != fingerprint(&existing.metadata()?) {
                        return Err(CoreError::new("TARGET_CHANGED", candidate.display()));
                    }
                    if same {
                        return Ok(CopyResult {
                            output: target.path.join(candidate),
                            sha256: digest,
                            skipped: true,
                            verified: true,
                        });
                    }
                    continue;
                }
                Err(e) => {
                    if e.code != "IO_NOT_FOUND" {
                        return Err(e);
                    }
                }
            }
            // Atomic no-clobber commit closes the existence-check race.
            progress(CopyProgress {
                bytes: copied,
                total: expected.bytes,
                phase: "committing",
            })?;
            match target.commit(&temp, &candidate) {
                Ok(()) => {
                    if verify && hash_at(target, &candidate, check)? != digest {
                        return Err(CoreError::new("CHECKSUM_MISMATCH", candidate.display()));
                    }
                    return Ok(CopyResult {
                        output: target.path.join(candidate),
                        sha256: digest,
                        skipped: false,
                        verified: verify,
                    });
                }
                Err(e) => {
                    if e.code == "IO_ALREADY_EXISTS" {
                        continue;
                    }
                    return Err(e);
                }
            }
        }
        Err(CoreError::new("TOO_MANY_CONFLICTS", output.display()))
    })();
    target.cleanup_temp(&temp, &temp_identity);
    result
}

pub fn write_new(root: &SafeDir, path: &Path, bytes: &[u8]) -> CoreResult<()> {
    let tmp = path.with_file_name(format!(".yingxu-part-{}", id()));
    let mut f = root.create(&tmp)?;
    let identity = fingerprint(&f.metadata()?).identity;
    let r = (|| {
        f.write_all(bytes)?;
        f.sync_all()?;
        root.commit(&tmp, path)
    })();
    root.cleanup_temp(&tmp, &identity);
    r
}

#[cfg(test)]
mod scan_tests {
    use super::*;
    use std::time::{SystemTime, UNIX_EPOCH};

    fn unique(tag: &str) -> PathBuf {
        let n = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let dir = std::env::temp_dir()
            .canonicalize()
            .unwrap()
            .join(format!("yingxu-scan-{tag}-{n}"));
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[test]
    fn unportable_names_are_skipped_not_fatal() {
        let dir = unique("colon");
        std::fs::write(dir.join("ok.jpg"), b"x").unwrap();
        std::fs::write(dir.join("weird 1:1.jpg"), b"x").unwrap();
        let report = scan_report(&dir).unwrap();
        assert_eq!(report.files.len(), 1, "正常文件应被收录");
        assert_eq!(report.skipped.len(), 1, "含冒号的文件应进跳过清单");
        assert_eq!(report.skipped[0].reason, "unportable_name");
        assert!(report.skipped[0].relative.contains(':'));
        // 旧接口保持"只给文件列表"的语义
        assert_eq!(scan(&dir).unwrap().len(), 1);
        std::fs::remove_dir_all(&dir).ok();
    }

    #[cfg(windows)]
    #[test]
    fn local_windows_paths_round_trip_and_network_paths_stay_rejected() {
        // select_directory 会把 canonicalize 的结果（\\?\C:\...）回传给前端，
        // 用户点「选择来源并扫描」时这个字符串又作为 source 传回来，
        // 所以 verbatim 盘符路径必须和普通盘符路径一样被接受。
        let root = unique("windows-local");
        let canonical = root.canonicalize().unwrap();
        assert!(
            canonical.to_string_lossy().starts_with(r"\\?\"),
            "canonicalize 在 Windows 上应返回 verbatim 路径：{}",
            canonical.display()
        );
        assert_eq!(local_directory(&root).unwrap(), canonical);
        assert_eq!(local_directory(&canonical).unwrap(), canonical);
        for path in [
            r"\\server\share",
            r"\\?\UNC\server\share",
            r"\\.\PhysicalDrive0",
            r"relative\folder",
        ] {
            assert_eq!(
                local_directory(Path::new(path)).unwrap_err().code,
                "LOCAL_PATH_REQUIRED"
            );
        }
        std::fs::remove_dir_all(&root).ok();
    }

    #[cfg(unix)]
    #[test]
    fn symlinks_are_skipped_not_fatal() {
        let dir = unique("symlink");
        std::fs::write(dir.join("real.jpg"), b"x").unwrap();
        std::os::unix::fs::symlink(dir.join("real.jpg"), dir.join("link.jpg")).unwrap();
        let report = scan_report(&dir).unwrap();
        assert_eq!(report.files.len(), 1);
        assert!(
            report
                .skipped
                .iter()
                .any(|s| s.reason == "symlink" && s.relative == "link.jpg"),
            "符号链接应被跳过并说明原因"
        );
        std::fs::remove_dir_all(&dir).ok();
    }
}
