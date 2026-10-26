//! File operations never overwrite an existing destination. Unix traversal uses
//! directory descriptors and O_NOFOLLOW, including every parent component.
use crate::model::*;
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
        use std::os::windows::fs::MetadataExt;
        format!("{:?}:{:?}", m.volume_serial_number(), m.file_index())
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
    if !path.is_absolute()
        || path.to_string_lossy().starts_with("//")
        || path.to_string_lossy().starts_with("\\\\")
    {
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

pub fn scan(root: &Path) -> CoreResult<Vec<ScannedFile>> {
    let dir = SafeDir::open(root)?;
    let mut result = vec![];
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
        let e = entry.map_err(|e| CoreError::new("SCAN_FAILED", e))?;
        if e.depth() == 0 {
            continue;
        }
        if e.file_type().is_symlink() {
            return Err(CoreError::new("SYMLINK_REJECTED", e.path().display()));
        }
        if e.file_type().is_dir() {
            continue;
        }
        let rel = e
            .path()
            .strip_prefix(&dir.path)
            .map_err(|e| CoreError::new("UNSAFE_PATH", e))?
            .to_path_buf();
        let f = dir.read(&rel)?;
        result.push(ScannedFile {
            kind: kind(&rel),
            relative: rel,
            fingerprint: fingerprint(&f.metadata()?),
            expected_sha256: None,
        });
    }
    Ok(result)
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
