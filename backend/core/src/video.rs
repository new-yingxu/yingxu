//! 视频元数据与抽帧（独立模块，不改 `media.rs`）。
//!
//! 元数据走纯 Rust 的 MP4/MOV box 解析，零外部依赖；抽帧是可选项：只有系统里存在
//! `ffmpeg`（或设置了 `YINGXU_FFMPEG`）才执行，否则明确记 `frame_extractor_unavailable`。
//! **本模块不含任何网络调用**：帧数据只落在本机缩略图目录，不上传。
use crate::{
    Engine,
    fsops::{self, SafeDir},
    model::*,
    raw::image_signals,
};
use serde_json::{Value, json};
use std::{
    fs::File,
    io::{Read, Seek, SeekFrom},
    path::{Path, PathBuf},
    process::{Child, Command, Stdio},
    time::{Duration, Instant},
};

const MOOV_LIMIT: u64 = 96 * 1024 * 1024;
const MAX_BOXES: usize = 8192;
const MAX_STSD_ENTRIES: u32 = 64;
const FFMPEG_TIMEOUT: Duration = Duration::from_secs(20);
const MAX_DURATION_MS: u64 = 6 * 60 * 60 * 1000;
pub const VIDEO_EXTENSIONS: &[&str] = &["mp4", "mov", "m4v", "qt"];

pub fn is_supported_container(path: &Path) -> bool {
    path.extension()
        .map(|e| VIDEO_EXTENSIONS.contains(&e.to_string_lossy().to_ascii_lowercase().as_str()))
        .unwrap_or(false)
}

#[derive(Debug, Default, Clone)]
pub struct VideoMeta {
    pub container: String,
    pub duration_ms: u64,
    pub width: u32,
    pub height: u32,
    pub rotation_deg: i32,
    pub fps: f64,
    pub video_codec: String,
    pub audio_codec: Option<String>,
    pub bitrate_bps: u64,
    pub created_at: Option<String>,
    pub has_audio: bool,
}

struct BoxRef {
    kind: [u8; 4],
    payload: u64,
    end: u64,
}

fn be32(b: &[u8]) -> u64 {
    u32::from_be_bytes([b[0], b[1], b[2], b[3]]) as u64
}

fn be16(b: &[u8]) -> u32 {
    u32::from_be_bytes([0, 0, b[0], b[1]])
}

fn read_at(f: &mut File, offset: u64, len: usize) -> Option<Vec<u8>> {
    f.seek(SeekFrom::Start(offset)).ok()?;
    let mut buf = vec![0u8; len];
    f.read_exact(&mut buf).ok()?;
    Some(buf)
}

/// 读一层子 box 列表。只记录偏移，不读负载。
fn child_boxes(f: &mut File, start: u64, end: u64) -> Vec<BoxRef> {
    let mut out = vec![];
    let mut pos = start;
    while pos + 8 <= end && out.len() < MAX_BOXES {
        let Some(head) = read_at(f, pos, 8) else {
            break;
        };
        let mut size = be32(&head[0..4]);
        let mut header = 8u64;
        if size == 1 {
            let Some(ext) = read_at(f, pos + 8, 8) else {
                break;
            };
            size = u64::from_be_bytes([
                ext[0], ext[1], ext[2], ext[3], ext[4], ext[5], ext[6], ext[7],
            ]);
            header = 16;
        } else if size == 0 {
            size = end - pos;
        }
        if size < header || pos + size > end {
            break;
        }
        out.push(BoxRef {
            kind: [head[4], head[5], head[6], head[7]],
            payload: pos + header,
            end: pos + size,
        });
        pos += size;
    }
    out
}

fn find<'a>(boxes: &'a [BoxRef], kind: &[u8; 4]) -> Option<&'a BoxRef> {
    boxes.iter().find(|b| &b.kind == kind)
}

fn find_all<'a>(boxes: &'a [BoxRef], kind: &[u8; 4]) -> Vec<&'a BoxRef> {
    boxes.iter().filter(|b| &b.kind == kind).collect()
}

fn mp4_time(secs: u64) -> Option<String> {
    // MP4 纪元为 1904-01-01 UTC，与 Unix 纪元差 2082844800 秒
    let unix = secs.checked_sub(2_082_844_800)?;
    chrono::DateTime::from_timestamp(i64::try_from(unix).ok()?, 0)
        .map(|t| t.to_rfc3339_opts(chrono::SecondsFormat::Secs, true))
}

struct Mvhd {
    timescale: u64,
    duration: u64,
    created: u64,
}

fn parse_mvhd(f: &mut File, b: &BoxRef) -> Option<Mvhd> {
    let head = read_at(f, b.payload, 32)?;
    let version = head[0];
    if version >= 1 {
        let created = u64::from_be_bytes([
            head[4], head[5], head[6], head[7], head[8], head[9], head[10], head[11],
        ]);
        Some(Mvhd {
            created,
            timescale: be32(&head[20..24]),
            duration: u64::from_be_bytes([
                head[24], head[25], head[26], head[27], head[28], head[29], head[30], head[31],
            ]),
        })
    } else {
        Some(Mvhd {
            created: be32(&head[4..8]),
            timescale: be32(&head[12..16]),
            duration: be32(&head[16..20]),
        })
    }
}

struct Tkhd {
    rotation: i32,
    width: u32,
    height: u32,
}

fn parse_tkhd(f: &mut File, b: &BoxRef) -> Option<Tkhd> {
    let head = read_at(f, b.payload, 96)?;
    let version = head[0];
    let matrix_at = if version >= 1 { 52 } else { 40 };
    let size_at = matrix_at + 36;
    let fixed = |o: usize| -> f64 {
        i32::from_be_bytes([head[o], head[o + 1], head[o + 2], head[o + 3]]) as f64 / 65536.0
    };
    let a = fixed(matrix_at);
    let bb = fixed(matrix_at + 4);
    let rotation = if a.abs() < 1e-6 && bb.abs() < 1e-6 {
        0
    } else {
        (bb.atan2(a).to_degrees().round() as i32).rem_euclid(360)
    };
    Some(Tkhd {
        rotation,
        width: (fixed(size_at) as u32).min(65535),
        height: (fixed(size_at + 4) as u32).min(65535),
    })
}

fn parse_stsd(f: &mut File, b: &BoxRef) -> Option<(String, u32, u32, bool)> {
    let head = read_at(f, b.payload, 12)?;
    let count = be32(&head[4..8]).min(MAX_STSD_ENTRIES as u64);
    let mut pos = b.payload + 8;
    let mut first_codec = None;
    let mut width = 0;
    let mut height = 0;
    let mut audio = false;
    for _ in 0..count {
        let entry = read_at(f, pos, 8)?;
        let size = be32(&entry[0..4]);
        if size < 16 || pos + size > b.end {
            break;
        }
        let codec = String::from_utf8_lossy(&entry[4..8]).to_string();
        let is_audio = matches!(codec.as_str(), "mp4a" | "ac-3" | "alac" | "opus" | "lpcm");
        if is_audio {
            audio = true;
        }
        if first_codec.is_none() {
            // 只有视频条目才带 VisualSampleEntry 的宽高
            if !is_audio
                && let Some(body) = read_at(f, pos, (size as usize).min(40))
                && body.len() >= 36
            {
                width = be16(&body[32..34]);
                height = be16(&body[34..36]);
            }
            first_codec = Some(codec);
        }
        pos += size;
    }
    Some((
        first_codec.unwrap_or_else(|| "unknown".into()),
        width,
        height,
        audio,
    ))
}

fn stsz_count(f: &mut File, b: &BoxRef) -> (u64, u64) {
    // 返回 (样本数, 总字节数)
    let Some(head) = read_at(f, b.payload, 12) else {
        return (0, 0);
    };
    let uniform = be32(&head[4..8]);
    let count = be32(&head[8..12]);
    if uniform > 0 {
        return (count, uniform * count);
    }
    let mut total = 0u64;
    let mut read = 0u64;
    while read < count {
        let take = (count - read).min(65536);
        let Some(chunk) = read_at(f, b.payload + 12 + read * 4, take as usize * 4) else {
            break;
        };
        for i in 0..take as usize {
            total += be32(&chunk[i * 4..i * 4 + 4]);
        }
        read += take;
    }
    (count, total)
}

/// 解析 MP4/MOV 元数据。整文件不入内存，moov 超过 96 MiB 直接拒绝。
pub fn probe(file: &mut File, file_len: u64) -> CoreResult<VideoMeta> {
    let head =
        read_at(file, 0, 12).ok_or_else(|| CoreError::new("VIDEO_UNREADABLE", "无法读取文件头"))?;
    if &head[4..8] != b"ftyp" {
        return Err(CoreError::new(
            "VIDEO_CONTAINER_UNSUPPORTED",
            "当前只解析 MP4/MOV 容器（ISO BMFF），其他封装暂不支持",
        ));
    }
    let brand = String::from_utf8_lossy(&head[8..12]).trim().to_string();
    let top = child_boxes(file, 0, file_len);
    let moov = find(&top, b"moov")
        .ok_or_else(|| CoreError::new("VIDEO_MOOV_MISSING", "缺少 moov，可能是未完成的录制文件"))?;
    if moov.end - moov.payload > MOOV_LIMIT {
        return Err(CoreError::new(
            "VIDEO_MOOV_TOO_LARGE",
            "moov 过大，拒绝解析",
        ));
    }
    let mut meta = VideoMeta {
        container: format!("{} ({brand})", String::from_utf8_lossy(&moov.kind)),
        ..Default::default()
    };
    let moov_boxes = child_boxes(file, moov.payload, moov.end);
    if let Some(mvhd) = find(&moov_boxes, b"mvhd").and_then(|b| parse_mvhd(file, b)) {
        meta.duration_ms = mvhd
            .duration
            .saturating_mul(1000)
            .checked_div(mvhd.timescale)
            .unwrap_or(0);
        meta.created_at = mp4_time(mvhd.created);
    }
    let mut total_samples = 0u64;
    let mut total_bytes = 0u64;
    let mut first_video = true;
    for trak in find_all(&moov_boxes, b"trak") {
        let trak_boxes = child_boxes(file, trak.payload, trak.end);
        if first_video
            && let Some(tkhd) = find(&trak_boxes, b"tkhd").and_then(|b| parse_tkhd(file, b))
        {
            meta.rotation_deg = tkhd.rotation;
            if tkhd.width > 0 && tkhd.height > 0 {
                meta.width = tkhd.width;
                meta.height = tkhd.height;
            }
        }
        let Some(mdia) = find(&trak_boxes, b"mdia") else {
            continue;
        };
        let mdia_boxes = child_boxes(file, mdia.payload, mdia.end);
        let handler = find(&mdia_boxes, b"hdlr")
            .and_then(|b| read_at(file, b.payload + 8, 4))
            .unwrap_or_default();
        let Some(minf) = find(&mdia_boxes, b"minf") else {
            continue;
        };
        let minf_boxes = child_boxes(file, minf.payload, minf.end);
        let Some(stbl) = find(&minf_boxes, b"stbl") else {
            continue;
        };
        let stbl_boxes = child_boxes(file, stbl.payload, stbl.end);
        let stsd = find(&stbl_boxes, b"stsd").and_then(|b| parse_stsd(file, b));
        if let (Some((codec, w, h, audio)), Some(stsz)) = (stsd, find(&stbl_boxes, b"stsz")) {
            let (count, bytes) = stsz_count(file, stsz);
            if handler == b"vide" {
                if first_video {
                    meta.video_codec = codec;
                    if meta.width == 0 && w > 0 {
                        meta.width = w;
                        meta.height = h;
                    }
                }
                first_video = false;
                total_samples += count;
                total_bytes += bytes;
            } else if handler == b"soun" || (handler.is_empty() && audio && !meta.has_audio) {
                meta.has_audio = true;
                meta.audio_codec = Some(codec);
            }
        } else if handler == b"vide" && first_video {
            first_video = false;
        }
    }
    if meta.duration_ms > 0 && total_samples > 0 {
        meta.fps = total_samples as f64 / (meta.duration_ms as f64 / 1000.0);
        if meta.fps > 0.0 {
            meta.fps = (meta.fps * 100.0).round() / 100.0;
        }
        meta.bitrate_bps = total_bytes.saturating_mul(8).saturating_mul(1000) / meta.duration_ms;
    }
    if meta.duration_ms == 0 && file_len > 0 {
        meta.bitrate_bps = 0;
    }
    Ok(meta)
}

/// 视频内嵌封面（`moov/udta/meta/ilst/covr`），有就直接当缩略图，不必抽帧。
pub fn cover_art(file: &mut File, file_len: u64) -> Option<Vec<u8>> {
    let top = child_boxes(file, 0, file_len);
    let moov = find(&top, b"moov")?;
    let moov_boxes = child_boxes(file, moov.payload, moov.end);
    let udta = find(&moov_boxes, b"udta")?;
    let udta_boxes = child_boxes(file, udta.payload, udta.end);
    let meta = find(&udta_boxes, b"meta")?;
    // meta 是 full box，子 box 从 +4 开始
    let ilst = child_boxes(file, meta.payload + 4, meta.end)
        .into_iter()
        .find(|b| &b.kind == b"ilst")?;
    let ilst_boxes = child_boxes(file, ilst.payload, ilst.end);
    let covr = find(&ilst_boxes, b"covr")?;
    let covr_boxes = child_boxes(file, covr.payload, covr.end);
    let data = find(&covr_boxes, b"data")?;
    if data.end - data.payload < 16 {
        return None;
    }
    // data box 前 8 字节是 type/locale，之后才是图片本体
    let len = usize::try_from(data.end - data.payload - 8).ok()?;
    read_at(file, data.payload + 8, len)
}

fn ffmpeg_bin() -> Option<String> {
    std::env::var("YINGXU_FFMPEG").ok().or_else(|| {
        std::env::var_os("PATH").and_then(|paths| {
            std::env::split_paths(&paths)
                .map(|p| p.join("ffmpeg"))
                .find(|p| p.is_file())
                .map(|p| p.to_string_lossy().into_owned())
        })
    })
}

/// 抽帧是否可用（只有本机存在 ffmpeg 或显式配置时才为 true）。
pub fn frame_extractor_present() -> bool {
    ffmpeg_bin().is_some()
}

fn wait_with_timeout(child: &mut Child, limit: Duration) -> bool {
    let started = Instant::now();
    loop {
        match child.try_wait() {
            Ok(Some(status)) => return status.success(),
            Ok(None) if started.elapsed() > limit => {
                let _ = child.kill();
                let _ = child.wait();
                return false;
            }
            Ok(None) => std::thread::sleep(Duration::from_millis(40)),
            Err(_) => return false,
        }
    }
}

/// 在 10%/50%/90% 位置各抽一帧（scaled 到 512 宽）。超时或缺失都返回空列表，不 panic。
/// 单帧抓取。`keyframe_only=true` 时只解关键帧（快，但落到的是最近的关键帧）。
fn grab_frame(bin: &str, file: &Path, at: f64, keyframe_only: bool) -> Option<Vec<u8>> {
    let tmp = std::env::temp_dir().join(format!("yingxu-frame-{}.jpg", id()));
    let mut cmd = Command::new(bin);
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        cmd.creation_flags(0x08000000); // CREATE_NO_WINDOW for background frame extraction.
    }
    cmd.args(["-v", "error", "-nostdin"]);
    if keyframe_only {
        // 只解 I 帧：定位到 seek 点之后的第一帧关键帧，避免逐帧解码
        cmd.args(["-skip_frame", "nokey"]);
    }
    cmd.args(["-ss", &format!("{at:.3}"), "-i"])
        .arg(file)
        .args([
            "-frames:v",
            "1",
            "-an",
            "-vf",
            "scale=512:-2",
            "-q:v",
            "4",
            "-f",
            "image2",
            "-y",
        ])
        .arg(&tmp)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null());
    let child = cmd.spawn().ok()?;
    let mut child = child;
    let ok = wait_with_timeout(&mut child, FFMPEG_TIMEOUT);
    let bytes = if ok {
        std::fs::read(&tmp)
            .ok()
            .filter(|b| !b.is_empty() && b.len() <= 4 * 1024 * 1024)
    } else {
        None
    };
    let _ = std::fs::remove_file(&tmp);
    bytes
}

/// 一帧抽出来的结果：标签、JPEG 字节、来源（`keyframe` / `nearest` / `embedded_cover`）。
pub type Frame = (String, Vec<u8>, &'static str);

/// 抽 10%/50%/90% 三帧，**关键帧优先**：先只解 I 帧，拿不到（例如关键帧间隔很大或
/// 片尾没有关键帧）再退回精确时间戳解码。每帧都带来源标记，便于排查。
pub fn extract_frames_with_modes(file: &Path, duration_ms: u64) -> (Vec<Frame>, String) {
    if duration_ms < 400 {
        return (vec![], "duration_too_short".into());
    }
    if duration_ms > MAX_DURATION_MS {
        return (vec![], "duration_over_limit".into());
    }
    let Some(bin) = ffmpeg_bin() else {
        return (vec![], "frame_extractor_unavailable".into());
    };
    let mut out = vec![];
    let (mut keyframes, mut exact) = (0usize, 0usize);
    for pct in [10u64, 50, 90] {
        let at = (duration_ms * pct / 100) as f64 / 1000.0;
        let label = format!("p{pct}");
        if let Some(bytes) = grab_frame(&bin, file, at, true) {
            keyframes += 1;
            out.push((label, bytes, "keyframe"));
            continue;
        }
        match grab_frame(&bin, file, at, false) {
            Some(bytes) => {
                exact += 1;
                out.push((label, bytes, "nearest"));
            }
            // 单帧失败不整体放弃：三帧里能出几帧算几帧
            None => {
                if out.is_empty() && pct == 90 {
                    return (out, "frame_extractor_failed".into());
                }
            }
        }
    }
    let source = match (keyframes, exact) {
        (0, 0) => "frame_extractor_failed",
        (_, 0) => "ffmpeg_keyframe_seek",
        (0, _) => "ffmpeg_stream_seek",
        _ => "ffmpeg_keyframe_then_nearest",
    };
    (out, source.into())
}

/// 兼容旧接口：只要帧字节，不要来源标记。
pub fn extract_frames(file: &Path, duration_ms: u64) -> (Vec<(String, Vec<u8>)>, String) {
    let (frames, source) = extract_frames_with_modes(file, duration_ms);
    (frames.into_iter().map(|(l, b, _)| (l, b)).collect(), source)
}

impl Engine {
    fn video_target(
        &self,
        media_id: &str,
    ) -> CoreResult<(PathBuf, SafeDir, PathBuf, Fingerprint, Value)> {
        let c = self.store.connect()?;
        let (path, kind, metadata): (String, String, String) = c.query_row(
            "SELECT path,kind,metadata FROM media_items WHERE id=?1",
            [media_id],
            |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)),
        )?;
        if kind != "video" {
            return Err(CoreError::new("VIDEO_REQUIRED", "该接口只分析视频"));
        }
        let path = PathBuf::from(path);
        if !is_supported_container(&path) {
            return Err(CoreError::new(
                "VIDEO_CONTAINER_UNSUPPORTED",
                "当前只支持 MP4/MOV，其他容器暂不支持",
            ));
        }
        let root = SafeDir::open(
            path.parent()
                .ok_or_else(|| CoreError::new("UNSAFE_PATH", "缺父目录"))?,
        )?;
        let rel = Path::new(path.file_name().unwrap()).to_path_buf();
        let fp = fsops::fingerprint(&root.read(&rel)?.metadata()?);
        let info: Value = serde_json::from_str(&metadata)?;
        if info.get("fingerprint").is_some_and(|f| *f != json!(fp)) {
            return Err(CoreError::new(
                "SOURCE_CHANGED",
                "索引后来源发生变化，请重新扫描",
            ));
        }
        Ok((path, root, rel, fp, info))
    }

    /// 只读元数据，速度快，用于列表页。
    pub fn probe_video(&self, media_id: &str) -> CoreResult<Value> {
        let (path, _, _, _, _) = self.video_target(media_id)?;
        let mut file = File::open(&path)?;
        let len = file.metadata()?.len();
        let meta = probe(&mut file, len)?;
        Ok(json!({"mediaId":media_id,"metadata":meta_json(&meta),"sourceReadOnly":true}))
    }

    /// 元数据 + 缩略图 + 待复核信号。`extract_frames=false` 时只取内嵌封面。
    pub fn analyze_video(&self, media_id: &str, extract: bool) -> CoreResult<Value> {
        let (path, root, rel, before, mut info) = self.video_target(media_id)?;
        let mut file = File::open(&path)?;
        let len = file.metadata()?.len();
        let meta = probe(&mut file, len)?;
        let mut frames: Vec<Frame> = vec![];
        let mut frame_source = String::from("disabled");
        if let Some(cover) = cover_art(&mut file, len) {
            frames.push(("cover".into(), cover, "embedded_cover"));
            frame_source = "embedded_cover".into();
        }
        if extract {
            // An embedded cover is only a poster image; it must not suppress
            // sampling actual footage for video quality analysis.
            let (extracted, source) = extract_frames_with_modes(&path, meta.duration_ms);
            if extracted.is_empty() {
                info["frameExtraction"] = json!(source);
                if frames.is_empty() {
                    frame_source = source;
                }
            } else {
                frames.extend(extracted);
                frame_source = if frames
                    .iter()
                    .any(|(_, _, frame_mode)| *frame_mode == "embedded_cover")
                {
                    format!("embedded_cover+{source}")
                } else {
                    source
                };
            }
        }
        let mut quality = json!({"status":"unanalyzed","automaticCleanupAllowed":false});
        let mut state = "unanalyzed";
        let mut thumbs = vec![];
        let mut signals = vec![];
        for (label, bytes, frame_mode) in &frames {
            let Ok(image) = image::load_from_memory(bytes) else {
                continue;
            };
            let (sig, sig_state, jpeg) = image_signals(&image);
            if state == "unanalyzed" && sig_state == "review" {
                state = "review";
            }
            if thumbs.is_empty() {
                let name = PathBuf::from("thumbnails").join(format!("{}-{}.jpg", media_id, id()));
                fsops::write_new(&SafeDir::open(&self.store.root)?, &name, &jpeg)?;
                thumbs.push(name);
            }
            let name =
                PathBuf::from("thumbnails").join(format!("video-{}-{}.jpg", media_id, label));
            // 分析统计由成功解码并计算出的帧决定；缓存只尝试安全的新建，既存派生图不覆盖。
            // 不能因 write_new 在重复分析时遇到已有文件，就把本次有效信号丢掉。
            let _ = fsops::write_new(&SafeDir::open(&self.store.root)?, &name, &jpeg);
            signals.push(json!({"frame":label,"frameMode":frame_mode,"width":sig["width"],"height":sig["height"],"highlightRatio":sig["highlightRatio"],"shadowRatio":sig["shadowRatio"],"laplacianVarianceAt512":sig["laplacianVarianceAt512"],"candidates":sig["candidates"]}));
        }
        if !signals.is_empty() {
            quality = json!({"status":"needs_human_review","model":"local-signals-v1","calibrated":false,"confidence":null,"frameSignals":signals,"candidates":signals.iter().flat_map(|s| s["candidates"].as_array().cloned().unwrap_or_default()).collect::<Vec<_>>(),"automaticCleanupAllowed":false});
        }
        info["analyzerVersion"] = json!("local-signals-v1");
        info["mediaKind"] = json!("video");
        info["video"] = meta_json(&meta);
        info["decodeStatus"] = json!(if frames.is_empty() {
            "metadata_only"
        } else {
            "frames_sampled"
        });
        info["frameSource"] = json!(frame_source);
        info["framesAnalyzed"] = json!(signals.len());
        info["quality"] = quality;
        info["analyzedLocally"] = json!(true);
        info["framesUploaded"] = json!(false);
        info["timestampSource"] = if meta.created_at.is_some() {
            json!("mp4_creation_time")
        } else {
            json!("filesystem_modified")
        };
        if let Some(created) = &meta.created_at {
            info["capturedAt"] = json!(created);
        }
        if info
            .get("humanReview")
            .and_then(|r| r.get("state"))
            .and_then(Value::as_str)
            .is_some_and(|r| ["good", "review", "unanalyzed"].contains(&r))
        {
            state = info["humanReview"]["state"].as_str().unwrap_or(state);
        }
        if fsops::fingerprint(&root.read(&rel)?.metadata()?) != before {
            return Err(CoreError::new(
                "SOURCE_CHANGED",
                "分析期间来源发生变化，未保存分析",
            ));
        }
        let mut conn = self.store.connect()?;
        let tx = conn.transaction_with_behavior(rusqlite::TransactionBehavior::Immediate)?;
        tx.execute(
            "UPDATE media_items SET metadata=?1,quality_state=?2 WHERE id=?3",
            rusqlite::params![info.to_string(), state, media_id],
        )?;
        if let Some(p) = thumbs.first() {
            tx.execute("INSERT INTO media_derivatives(media_id,path,source_fingerprint,created_at) VALUES(?1,?2,?3,?4) ON CONFLICT(media_id) DO UPDATE SET path=excluded.path,source_fingerprint=excluded.source_fingerprint,created_at=excluded.created_at",rusqlite::params![media_id,p.to_string_lossy(),serde_json::to_string(&before)?,now()])?;
        }
        tx.commit()?;
        Ok(json!({
            "mediaId":media_id,
            "qualityState":state,
            "metadata":info,
            "frameSource":frame_source,
            "thumbnailAvailable":!thumbs.is_empty(),
            "sourceReadOnly":true,
            "framesUploaded":false
        }))
    }
}

fn meta_json(m: &VideoMeta) -> Value {
    json!({
        "container":m.container,
        "durationMs":m.duration_ms,
        "width":m.width,
        "height":m.height,
        "rotationDeg":m.rotation_deg,
        "fps":m.fps,
        "videoCodec":m.video_codec,
        "audioCodec":m.audio_codec,
        "hasAudio":m.has_audio,
        "bitrateBps":m.bitrate_bps,
        "createdAt":m.created_at
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn be32b(v: u32) -> [u8; 4] {
        v.to_be_bytes()
    }

    fn bx(kind: &[u8; 4], payload: &[u8]) -> Vec<u8> {
        let mut out = be32b(payload.len() as u32 + 8).to_vec();
        out.extend_from_slice(kind);
        out.extend_from_slice(payload);
        out
    }

    fn mvhd(timescale: u32, duration: u32) -> Vec<u8> {
        let mut p = vec![0u8; 100];
        p[0] = 0; // version 0
        p[4..8].copy_from_slice(&3_700_000_000u32.to_be_bytes()); // 1904 纪元秒
        p[12..16].copy_from_slice(&timescale.to_be_bytes());
        p[16..20].copy_from_slice(&duration.to_be_bytes());
        bx(b"mvhd", &p)
    }

    fn tkhd(width: u32, height: u32, rotate: bool) -> Vec<u8> {
        let mut p = vec![0u8; 84];
        let at = 40;
        let a: i32 = if rotate { 0 } else { 65536 };
        let b: i32 = if rotate { 65536 } else { 0 };
        p[at..at + 4].copy_from_slice(&a.to_be_bytes());
        p[at + 4..at + 8].copy_from_slice(&b.to_be_bytes());
        p[76..80].copy_from_slice(&(width << 16).to_be_bytes());
        p[80..84].copy_from_slice(&(height << 16).to_be_bytes());
        bx(b"tkhd", &p)
    }

    fn stsd(codec: &[u8; 4], width: u16, height: u16) -> Vec<u8> {
        let mut entry = vec![0u8; 78];
        entry[32..34].copy_from_slice(&width.to_be_bytes());
        entry[34..36].copy_from_slice(&height.to_be_bytes());
        let mut p = vec![0u8; 8];
        p[4..8].copy_from_slice(&1u32.to_be_bytes());
        p.extend_from_slice(&be32b(entry.len() as u32 + 8));
        p.extend_from_slice(codec);
        p.extend_from_slice(&entry);
        bx(b"stsd", &p)
    }

    fn stsz(count: u32, uniform: u32) -> Vec<u8> {
        let mut p = vec![0u8; 12];
        p[4..8].copy_from_slice(&uniform.to_be_bytes());
        p[8..12].copy_from_slice(&count.to_be_bytes());
        bx(b"stsz", &p)
    }

    fn trak(
        handler: &[u8; 4],
        tkhd_box: Option<Vec<u8>>,
        stsd_box: Vec<u8>,
        samples: u32,
    ) -> Vec<u8> {
        let mut hdlr = vec![0u8; 12];
        hdlr[8..12].copy_from_slice(handler);
        let stbl = bx(b"stbl", &[stsd_box, stsz(samples, 1000)].concat());
        let minf = bx(b"minf", &stbl);
        let mdia = bx(
            b"mdia",
            &[bx(b"mdhd", &[0u8; 24]), bx(b"hdlr", &hdlr), minf].concat(),
        );
        let mut parts = vec![];
        if let Some(t) = tkhd_box {
            parts.push(t);
        }
        parts.push(mdia);
        bx(b"trak", &parts.concat())
    }

    fn sample_mov() -> Vec<u8> {
        let mut ftyp = be32b(20).to_vec();
        ftyp.extend_from_slice(b"ftypqt  ");
        ftyp.extend_from_slice(&[0u8; 8]);
        let moov = bx(
            b"moov",
            &[
                mvhd(600, 12_000),
                trak(
                    b"vide",
                    Some(tkhd(1920, 1080, true)),
                    stsd(b"avc1", 1920, 1080),
                    240,
                ),
                trak(b"soun", None, stsd(b"mp4a", 0, 0), 400),
            ]
            .concat(),
        );
        [ftyp, moov].concat()
    }

    #[allow(unused_imports)]
    use std::io::Write;

    fn write_tmp(name: &str, bytes: &[u8]) -> PathBuf {
        let dir = std::env::temp_dir()
            .canonicalize()
            .unwrap()
            .join(format!("yingxu-video-{}", id()));
        std::fs::create_dir_all(&dir).unwrap();
        let p = dir.join(name);
        let mut f = std::fs::File::create(&p).unwrap();
        f.write_all(bytes).unwrap();
        p
    }

    #[test]
    fn mov_metadata_is_parsed() {
        let p = write_tmp("clip.mov", &sample_mov());
        let mut f = std::fs::File::open(&p).unwrap();
        let len = f.metadata().unwrap().len();
        let meta = probe(&mut f, len).unwrap();
        assert_eq!(meta.duration_ms, 20_000); // 12000/600 秒
        assert_eq!(meta.width, 1920);
        assert_eq!(meta.height, 1080);
        assert_eq!(meta.rotation_deg, 90);
        assert_eq!(meta.video_codec, "avc1");
        assert_eq!(meta.audio_codec.as_deref(), Some("mp4a"));
        assert!(meta.has_audio);
        assert!((meta.fps - 12.0).abs() < 0.01);
        assert!(meta.bitrate_bps > 0);
        assert!(meta.created_at.is_some());
    }

    #[test]
    fn non_bmff_container_is_explicitly_rejected() {
        let p = write_tmp("clip.mkv", b"\x1aE\xdf\xa3not-an-mp4");
        let mut f = std::fs::File::open(&p).unwrap();
        let len = f.metadata().unwrap().len();
        assert_eq!(
            probe(&mut f, len).unwrap_err().code,
            "VIDEO_CONTAINER_UNSUPPORTED"
        );
    }

    #[test]
    fn missing_moov_is_reported_not_panicking() {
        let mut bytes = be32b(20).to_vec();
        bytes.extend_from_slice(b"ftypmp42");
        bytes.extend_from_slice(&[0u8; 8]);
        let p = write_tmp("broken.mp4", &bytes);
        let mut f = std::fs::File::open(&p).unwrap();
        let len = f.metadata().unwrap().len();
        assert_eq!(probe(&mut f, len).unwrap_err().code, "VIDEO_MOOV_MISSING");
    }

    fn sample_mov_with_embedded_cover() -> (Vec<u8>, Vec<u8>) {
        let mut jpeg = std::io::Cursor::new(Vec::new());
        image::DynamicImage::new_rgb8(16, 16)
            .write_to(&mut jpeg, image::ImageFormat::Jpeg)
            .unwrap();
        let jpeg = jpeg.into_inner();
        let mut data_payload = vec![0, 0, 0, 13, 0, 0, 0, 0]; // JPEG cover type + locale
        data_payload.extend_from_slice(&jpeg);
        let cover = bx(b"covr", &bx(b"data", &data_payload));
        let ilst = bx(b"ilst", &cover);
        let meta = bx(b"meta", &[vec![0u8; 4], ilst].concat());
        let udta = bx(b"udta", &meta);

        let base = sample_mov();
        let ftyp = base[..20].to_vec();
        let moov_payload = &base[28..];
        let moov = bx(b"moov", &[moov_payload, udta.as_slice()].concat());
        ([ftyp, moov].concat(), jpeg)
    }

    #[test]
    fn repeated_video_analysis_keeps_frame_signals_when_cache_exists() {
        let (bytes, _) = sample_mov_with_embedded_cover();
        let source_dir = std::env::temp_dir()
            .canonicalize()
            .unwrap()
            .join(format!("yingxu-video-repeat-source-{}", id()));
        std::fs::create_dir_all(&source_dir).unwrap();
        let source = source_dir.join("clip.mov");
        std::fs::write(&source, &bytes).unwrap();
        let before_modified = std::fs::metadata(&source).unwrap().modified().unwrap();

        let engine = Engine::open(
            std::env::temp_dir()
                .canonicalize()
                .unwrap()
                .join(format!("yingxu-video-repeat-store-{}", id())),
        )
        .unwrap();
        engine.index_source(&source_dir).unwrap();
        let media_id: String = engine
            .store
            .connect()
            .unwrap()
            .query_row(
                "SELECT id FROM media_items WHERE name='clip.mov'",
                [],
                |r| r.get(0),
            )
            .unwrap();

        let mut fixed_thumbnail = None;
        for _ in 0..3 {
            let out = engine.analyze_video(&media_id, false).unwrap();
            let metadata = &out["metadata"];
            assert_eq!(metadata["framesAnalyzed"], 1);
            assert_eq!(
                metadata["quality"]["frameSignals"]
                    .as_array()
                    .unwrap()
                    .len(),
                1
            );
            assert_eq!(metadata["decodeStatus"], "frames_sampled");
            assert_eq!(metadata["frameSource"], "embedded_cover");
            assert_eq!(metadata["framesUploaded"], false);
            assert_eq!(out["framesUploaded"], false);

            let thumb = engine
                .store
                .root
                .join("thumbnails")
                .join(format!("video-{media_id}-cover.jpg"));
            if let Some(original) = &fixed_thumbnail {
                assert_eq!(std::fs::read(&thumb).unwrap(), *original);
            } else {
                fixed_thumbnail = Some(std::fs::read(thumb).unwrap());
            }
            assert_eq!(std::fs::read(&source).unwrap(), bytes);
            assert_eq!(
                std::fs::metadata(&source).unwrap().modified().unwrap(),
                before_modified
            );
        }
    }

    #[test]
    fn analyze_video_on_sample_writes_metadata_without_frames() {
        let bytes = sample_mov();
        let dir = std::env::temp_dir()
            .canonicalize()
            .unwrap()
            .join(format!("yingxu-video-e2e-{}", id()));
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(dir.join("clip.mov"), &bytes).unwrap();
        let engine = Engine::open(
            std::env::temp_dir()
                .canonicalize()
                .unwrap()
                .join(format!("yingxu-video-store-{}", id())),
        )
        .unwrap();
        engine.index_source(&dir).unwrap();
        let media_id: String = engine
            .store
            .connect()
            .unwrap()
            .query_row(
                "SELECT id FROM media_items WHERE name='clip.mov'",
                [],
                |r| r.get(0),
            )
            .unwrap();
        let out = engine.analyze_video(&media_id, false).unwrap();
        assert_eq!(out["metadata"]["video"]["durationMs"], 20_000);
        assert_eq!(out["framesUploaded"], false);
        assert_eq!(std::fs::read(dir.join("clip.mov")).unwrap(), bytes);
    }
}
