//! 存储卡 / 可移动磁盘识别。
//!
//! 目标：识别"是哪一张卡"，而不是"当前挂载在哪"。
//! 因此每张卡生成一个跨插拔稳定的 `mount_key`（优先硬件序列号 / 卷 UUID，
//! 其次卷名，最后才退回挂载路径），名称与备注按这个身份保存在本机数据库。

use serde::{Deserialize, Serialize};
use std::path::{Path, PathBuf};

/// 一个卷（存储卡 / 移动盘 / 本机磁盘）的系统级信息。
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct VolumeInfo {
    /// 跨插拔稳定的卡片身份标识；盘符漂移不影响。
    pub mount_key: String,
    pub mount_point: PathBuf,
    /// 系统卷标（Windows/Mac 上用户在系统里看到的盘名）。
    pub label: String,
    /// removable（存储卡 / 移动盘）| fixed（内置盘）| unknown
    pub kind: String,
    pub file_system: String,
    pub total_bytes: u64,
    pub available_bytes: u64,
}

/// 卷信息 + 本机保存的卡片名称与备注。
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct VolumeCard {
    #[serde(flatten)]
    pub volume: VolumeInfo,
    pub custom_name: String,
    pub note: String,
}

/// 数据库中按卡片身份保存的名称与备注。
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct CardLabel {
    pub mount_key: String,
    pub name: String,
    pub note: String,
    pub updated_at: String,
}

pub const CARD_NAME_MAX: usize = 80;
pub const CARD_NOTE_MAX: usize = 300;

pub fn validate_card_label(mount_key: &str, name: &str, note: &str) -> Result<(), String> {
    let key = mount_key.trim();
    if key.is_empty() || key.len() > 200 {
        return Err("卡片标识无效".into());
    }
    if key.chars().any(char::is_control) {
        return Err("卡片标识包含非法字符".into());
    }
    if name.chars().count() > CARD_NAME_MAX {
        return Err(format!("名称最多 {CARD_NAME_MAX} 字"));
    }
    if note.chars().count() > CARD_NOTE_MAX {
        return Err(format!("备注最多 {CARD_NOTE_MAX} 字"));
    }
    Ok(())
}

/// 生成稳定的卡片身份：优先硬件序列号 / UUID，最后才退回挂载路径。
pub fn mount_key(platform_tag: &str, hardware_id: Option<&str>, mount_point: &Path) -> String {
    if let Some(serial) = hardware_id {
        let serial = serial.trim();
        if !serial.is_empty()
            && serial.len() <= 128
            && serial
                .chars()
                .all(|c| c.is_ascii_alphanumeric() || c == '-' || c == ':')
        {
            return format!("{platform_tag}-{serial}");
        }
    }
    let path = mount_point.to_string_lossy().replace('\\', "/");
    format!(
        "{platform_tag}-path-{}",
        path.trim_end_matches('/').to_lowercase()
    )
}

pub fn list_volumes() -> Vec<VolumeInfo> {
    platform::list()
}

#[cfg(target_os = "windows")]
mod platform {
    use super::{VolumeInfo, mount_key};
    use std::path::{Path, PathBuf};
    use windows_sys::Win32::Storage::FileSystem::{
        GetDiskFreeSpaceExW, GetDriveTypeW, GetLogicalDrives, GetVolumeInformationW,
    };

    pub(super) fn list() -> Vec<VolumeInfo> {
        let mut result = vec![];
        let drives = unsafe { GetLogicalDrives() };
        if drives == 0 {
            return result;
        }
        for index in 0..26u32 {
            if drives & (1u32 << index) == 0 {
                continue;
            }
            let letter = (b'A' + index as u8) as char;
            let root = format!("{letter}:\\");
            let root_w: Vec<u16> = root.encode_utf16().chain([0]).collect();
            let kind = match unsafe { GetDriveTypeW(root_w.as_ptr()) } {
                // GetDriveTypeW 返回值：2=可移动介质（存储卡/U盘），3=固定磁盘
                2 => "removable",
                3 => "fixed",
                _ => "unknown",
            };
            let mut label_buf = [0u16; 33];
            let mut serial: u32 = 0;
            let mut fs_buf = [0u16; 32];
            let mut label = String::new();
            let mut file_system = String::new();
            if unsafe {
                GetVolumeInformationW(
                    root_w.as_ptr(),
                    label_buf.as_mut_ptr(),
                    label_buf.len() as u32,
                    &mut serial,
                    std::ptr::null_mut(),
                    std::ptr::null_mut(),
                    fs_buf.as_mut_ptr(),
                    fs_buf.len() as u32,
                )
            } != 0
            {
                label = utf16_to_string(&label_buf);
                file_system = utf16_to_string(&fs_buf);
            }
            let mut available: u64 = 0;
            let mut total: u64 = 0;
            let mut free: u64 = 0;
            if unsafe {
                GetDiskFreeSpaceExW(root_w.as_ptr(), &mut available, &mut total, &mut free)
            } == 0
            {
                // 无介质（空读卡器）或无法读取的驱动器，跳过。
                continue;
            }
            let hardware_id = if serial != 0 {
                Some(format!("serial-{serial:08X}"))
            } else {
                None
            };
            result.push(VolumeInfo {
                mount_key: mount_key("win", hardware_id.as_deref(), Path::new(&root)),
                mount_point: PathBuf::from(&root),
                label,
                kind: kind.into(),
                file_system,
                total_bytes: total,
                available_bytes: available,
            });
        }
        result
    }

    fn utf16_to_string(buf: &[u16]) -> String {
        let end = buf.iter().position(|&c| c == 0).unwrap_or(buf.len());
        String::from_utf16_lossy(&buf[..end])
    }
}

#[cfg(unix)]
mod platform {
    use super::{VolumeInfo, mount_key};
    use std::ffi::CString;
    use std::path::{Path, PathBuf};

    /// `diskutil info <mount>` 文本输出中解析出的卷身份。
    #[derive(Debug, Default, Clone, PartialEq, Eq)]
    pub(super) struct DiskutilInfo {
        pub uuid: String,
        pub volume_name: String,
        pub file_system: String,
        pub internal: Option<bool>,
    }

    /// 纯函数，便于单测。仅在 macOS 编译（Linux 构建不调用 diskutil）。
    #[cfg(target_os = "macos")]
    pub(super) fn parse_diskutil_info(text: &str) -> DiskutilInfo {
        let mut info = DiskutilInfo::default();
        for line in text.lines() {
            let Some((key, value)) = line.split_once(':') else {
                continue;
            };
            let (key, value) = (key.trim(), value.trim());
            match key {
                "Volume UUID" => info.uuid = value.to_string(),
                "Volume Name" => info.volume_name = value.to_string(),
                "File System Personality" => info.file_system = value.to_string(),
                "Internal" => info.internal = Some(value.eq_ignore_ascii_case("yes")),
                _ => {}
            }
        }
        info
    }

    /// 解析 /proc/mounts，只保留本地文件系统挂载点（Linux 开发环境用）。纯函数，便于单测。
    pub(super) fn parse_proc_mounts(text: &str) -> Vec<(String, String, String)> {
        let virtual_fs = [
            "proc",
            "sysfs",
            "devpts",
            "tmpfs",
            "devtmpfs",
            "squashfs",
            "cgroup",
            "cgroup2",
            "overlay",
            "mqueue",
            "shm",
            "efivarfs",
            "bpf",
            "tracefs",
            "debugfs",
            "binfmt_misc",
            "fusectl",
            "securityfs",
            "pstore",
            "configfs",
            "hugetlbfs",
            "ramfs",
            "autofs",
            "nsfs",
        ];
        let local_fs = [
            "ext2", "ext3", "ext4", "xfs", "btrfs", "f2fs", "ntfs", "ntfs3", "vfat", "exfat", "udf",
        ];
        text.lines()
            .filter_map(|line| {
                let mut parts = line.split_whitespace();
                let device = parts.next()?.to_string();
                let mount = parts.next()?.to_string();
                let fstype = parts.next()?.to_string();
                let mount = mount.replace("\\040", " ").replace("\\134", "\\");
                if virtual_fs.contains(&fstype.as_str()) || mount.starts_with("/snap") {
                    return None;
                }
                if local_fs.contains(&fstype.as_str()) || device.starts_with("/dev/") {
                    Some((device, mount, fstype))
                } else {
                    None
                }
            })
            .collect()
    }

    /// 去掉分区后缀：sda1 -> sda，nvme0n1p3 -> nvme0n1。
    pub(super) fn strip_partition(name: &str) -> String {
        let mut s = name.to_string();
        while s.ends_with(|c: char| c.is_ascii_digit()) {
            s.pop();
        }
        if s.len() > 1
            && s.ends_with('p')
            && s[..s.len() - 1]
                .chars()
                .last()
                .is_some_and(|c| c.is_ascii_digit())
        {
            s.pop();
        }
        s
    }

    fn linux_kind(device: &str) -> String {
        let base = strip_partition(device.trim_start_matches("/dev/"));
        if base.starts_with("mmcblk") || base.starts_with("sr") {
            return "removable".into();
        }
        if let Ok(value) = std::fs::read_to_string(format!("/sys/block/{base}/removable")) {
            return if value.trim() == "1" {
                "removable".into()
            } else {
                "fixed".into()
            };
        }
        "fixed".into()
    }

    fn statvfs_bytes(path: &Path) -> Option<(u64, u64)> {
        let c = CString::new(path.as_os_str().to_string_lossy().as_bytes()).ok()?;
        let mut fs: libc::statvfs = unsafe { std::mem::zeroed() };
        if unsafe { libc::statvfs(c.as_ptr(), &mut fs) } != 0 || fs.f_frsize == 0 {
            return None;
        }
        Some((
            fs.f_blocks as u64 * fs.f_frsize as u64,
            fs.f_bavail as u64 * fs.f_frsize as u64,
        ))
    }

    pub(super) fn list() -> Vec<VolumeInfo> {
        let platform_tag = if cfg!(target_os = "macos") {
            "mac"
        } else {
            "linux"
        };
        // (设备, 挂载点, 文件系统)
        let mut mounts: Vec<(String, String, String)> =
            vec![(String::new(), "/".into(), String::new())];
        if cfg!(target_os = "macos") {
            if let Ok(entries) = std::fs::read_dir("/Volumes") {
                for entry in entries.flatten() {
                    mounts.push((
                        String::new(),
                        entry.path().to_string_lossy().to_string(),
                        String::new(),
                    ));
                }
            }
        } else if let Ok(text) = std::fs::read_to_string("/proc/mounts") {
            mounts = parse_proc_mounts(&text);
        }
        let mut result = vec![];
        for (device, mount, fstype) in mounts {
            let path = PathBuf::from(&mount);
            let Some((total, available)) = statvfs_bytes(&path) else {
                continue;
            };
            if total == 0 {
                continue;
            }
            let (kind, file_system, hardware_id, label) = if cfg!(target_os = "macos") {
                let info = diskutil_identity(&mount);
                let kind = if mount == "/" || info.internal != Some(false) {
                    "fixed".to_string()
                } else {
                    "removable".to_string()
                };
                let label = if info.volume_name.is_empty() {
                    path.file_name()
                        .map(|n| n.to_string_lossy().to_string())
                        .unwrap_or_default()
                } else {
                    info.volume_name.clone()
                };
                let hardware_id = if info.uuid.is_empty() {
                    None
                } else {
                    Some(format!("uuid-{}", info.uuid))
                };
                (kind, info.file_system, hardware_id, label)
            } else {
                let kind = if mount == "/" {
                    "fixed".to_string()
                } else {
                    linux_kind(&device)
                };
                (kind, fstype, None, String::new())
            };
            result.push(VolumeInfo {
                mount_key: mount_key(platform_tag, hardware_id.as_deref(), &path),
                mount_point: path,
                label,
                kind,
                file_system,
                total_bytes: total,
                available_bytes: available,
            });
        }
        result
    }

    #[cfg(target_os = "macos")]
    fn diskutil_identity(mount: &str) -> DiskutilInfo {
        std::process::Command::new("/usr/sbin/diskutil")
            .arg("info")
            .arg(mount)
            .output()
            .ok()
            .filter(|output| output.status.success())
            .map(|output| parse_diskutil_info(&String::from_utf8_lossy(&output.stdout)))
            .unwrap_or_default()
    }

    #[cfg(not(target_os = "macos"))]
    fn diskutil_identity(_mount: &str) -> DiskutilInfo {
        DiskutilInfo::default()
    }
}

#[cfg(not(any(unix, target_os = "windows")))]
mod platform {
    use super::VolumeInfo;

    pub(super) fn list() -> Vec<VolumeInfo> {
        vec![]
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn mount_key_prefers_hardware_id() {
        assert_eq!(
            mount_key("win", Some("serial-1A2B3C4D"), Path::new("E:\\")),
            "win-serial-1A2B3C4D"
        );
    }

    #[test]
    fn mount_key_falls_back_to_normalized_path() {
        assert_eq!(
            mount_key("mac", Some(""), Path::new("/Volumes/SD")),
            "mac-path-/volumes/sd"
        );
        assert_eq!(mount_key("win", None, Path::new("E:\\")), "win-path-e:");
    }

    #[test]
    fn mount_key_rejects_dirty_hardware_id() {
        assert_eq!(
            mount_key("win", Some("bad id 😊"), Path::new("E:\\")),
            "win-path-e:"
        );
    }

    #[test]
    fn validate_card_label_bounds() {
        assert!(validate_card_label("win-serial-1A2B3C4D", "云南旅拍", "客户成片已交付").is_ok());
        assert!(validate_card_label("", "x", "").is_err());
        let long_name = "卡".repeat(CARD_NAME_MAX + 1);
        let long_note = "备".repeat(CARD_NOTE_MAX + 1);
        assert!(validate_card_label("win-serial-1A2B3C4D", &long_name, "").is_err());
        assert!(validate_card_label("win-serial-1A2B3C4D", "", &long_note).is_err());
        assert!(validate_card_label("bad\u{7}key", "", "").is_err());
    }

    #[cfg(target_os = "macos")]
    #[test]
    fn parses_diskutil_info_output() {
        let text = "Device Identifier: disk4s1\nDevice Node: /dev/disk4s1\n  Volume Name:              SDXC\n  Internal:                 No\n  Volume UUID:              01234567-89AB-CDEF-0123-456789ABCDEF\n  File System Personality:  exFAT\n";
        let info = platform::parse_diskutil_info(text);
        assert_eq!(info.volume_name, "SDXC");
        assert_eq!(info.uuid, "01234567-89AB-CDEF-0123-456789ABCDEF");
        assert_eq!(info.file_system, "exFAT");
        assert_eq!(info.internal, Some(false));
    }

    #[cfg(target_os = "macos")]
    #[test]
    fn parses_diskutil_info_defaults_on_missing_fields() {
        let info = platform::parse_diskutil_info("Device Identifier: disk1s1\n");
        assert_eq!(info, platform::DiskutilInfo::default());
    }

    #[cfg(target_os = "linux")]
    #[test]
    fn parses_proc_mounts_and_filters_virtual_fs() {
        let text = "/dev/nvme0n1p2 / ext4 rw 0 0\nproc /proc proc rw 0 0\n/dev/sdb1 /media/user/SD exfat rw 0 0\n/dev/nvme0n1p1 /boot/efi vfat rw 0 0\n";
        let mounts = platform::parse_proc_mounts(text);
        assert_eq!(mounts.len(), 3);
        assert_eq!(mounts[1].0, "/dev/sdb1");
        assert_eq!(platform::strip_partition("sda1"), "sda");
        assert_eq!(platform::strip_partition("nvme0n1p3"), "nvme0n1");
        assert_eq!(platform::strip_partition("mmcblk0p2"), "mmcblk0");
    }
}
