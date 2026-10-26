//! RAW 只读预览与元数据提取（独立模块，不改 `media.rs`）。
//!
//! 边界：**只做读取与内嵌预览**，不做高质量显影。多数 RAW 内含 JPEG 预览图，
//! 提取预览做缩略图比完整解码快一个数量级，也避免引入 C 依赖与许可负担。
//! 原片全程只读：不写、不重命名、不删除，出口前复核 fingerprint。
use crate::{
    Engine,
    fsops::{self, SafeDir},
    model::*,
};
use image::{DynamicImage, ImageReader, Limits, codecs::jpeg::JpegEncoder};
use serde_json::{Value, json};
use std::{
    fs::File,
    io::{BufReader, Cursor, Read, Seek, SeekFrom},
    path::{Path, PathBuf},
};

/// 当前识别为 RAW 的扩展名。与 `fsops::kind` 的 photo 归类一致，另补主流补充后缀。
pub const RAW_EXTENSIONS: &[&str] = &[
    "cr2", "cr3", "crw", "nef", "nrw", "arw", "srf", "sr2", "raf", "dng", "orf", "rw2", "raw",
    "pef", "srw", "x3f", "3fr", "fff", "iiq", "mos", "mrw", "erf", "kdc", "dcr", "rwl",
];
/// 预览搜索上限：超过此偏移仍找不到内嵌预览即判定不可用，避免整文件读入。
const SCAN_LIMIT: u64 = 64 * 1024 * 1024;
/// 单个预览上限。
const MAX_PREVIEW: u64 = 32 * 1024 * 1024;
const MAX_EDGE: u32 = 16384;
const MAX_ALLOC: u64 = 256 * 1024 * 1024;
const MAX_IFD_ENTRIES: usize = 4096;
const MAX_IFD_DEPTH: u8 = 3;

pub fn is_raw_extension(ext: &str) -> bool {
    RAW_EXTENSIONS.contains(&ext.to_ascii_lowercase().as_str())
}

pub fn is_raw_path(path: &Path) -> bool {
    path.extension()
        .map(|e| is_raw_extension(&e.to_string_lossy()))
        .unwrap_or(false)
}

/// 按文件头判断容器，不信任扩展名。
pub fn detect_format(head: &[u8]) -> &'static str {
    match head {
        [b'F', b'U', b'J', b'I', b'F', b'I', b'L', b'M', ..] => "fujifilm_raf",
        [b'I', b'I', 0x2a, 0x00, ..] => "tiff_le",
        [b'M', b'M', 0x00, 0x2a, ..] => "tiff_be",
        [b'I', b'I', 0x2b, 0x00, ..] => "bigtiff_le",
        [b'M', b'M', 0x00, 0x2b, ..] => "bigtiff_be",
        _ if head.len() >= 12 && &head[4..8] == b"ftyp" => match &head[8..12] {
            b"crx " => "cr3",
            b"heic" | b"heix" | b"mif1" => "heif",
            _ => "iso_bmff",
        },
        [b'F', b'U', b'J', ..] => "fujifilm_raf",
        _ => "unknown",
    }
}

#[derive(Debug)]
pub struct RawPreview {
    pub format: &'static str,
    pub bytes: Vec<u8>,
    pub offset: u64,
    /// `tiff_ifd` / `raf_header` / `jpeg_scan`
    pub source: &'static str,
}

struct Entry {
    tag: u16,
    typ: u16,
    count: u32,
    val: u32,
}

fn num(b: &[u8], be: bool, wide: bool) -> u32 {
    let n = if wide { 4 } else { 2 };
    let s = &b[..n.min(b.len())];
    let mut v = 0u32;
    if be {
        for x in s {
            v = (v << 8) | u32::from(*x);
        }
    } else {
        for (i, x) in s.iter().enumerate() {
            v |= u32::from(*x) << (8 * i);
        }
    }
    v
}

fn read_at(f: &mut File, offset: u64, len: usize, file_len: u64) -> Option<Vec<u8>> {
    if len == 0 || offset.checked_add(len as u64)? > file_len {
        return None;
    }
    f.seek(SeekFrom::Start(offset)).ok()?;
    let mut buf = vec![0u8; len];
    f.read_exact(&mut buf).ok()?;
    Some(buf)
}

/// 解析一个 IFD，返回条目与下一个 IFD 偏移。
fn read_ifd(f: &mut File, be: bool, offset: u64, file_len: u64) -> Option<(Vec<Entry>, u64)> {
    if offset < 8 || offset + 2 > file_len {
        return None;
    }
    let head = read_at(f, offset, 2, file_len)?;
    let count = num(&head, be, false) as usize;
    if count == 0 || count > MAX_IFD_ENTRIES {
        return None;
    }
    let raw = read_at(f, offset + 2, count * 12 + 4, file_len)?;
    let mut entries = Vec::with_capacity(count);
    for i in 0..count {
        let e = &raw[i * 12..i * 12 + 12];
        entries.push(Entry {
            tag: num(&e[0..2], be, false) as u16,
            typ: num(&e[2..4], be, false) as u16,
            count: num(&e[4..8], be, true),
            val: num(&e[8..12], be, true),
        });
    }
    let next = num(&raw[count * 12..], be, true);
    Some((entries, u64::from(next)))
}

fn entry_value(e: &Entry, be: bool) -> u64 {
    // SHORT/BYTE 只有 2 字节有效，内联在 4 字节值的头部
    if matches!(e.typ, 1 | 3) {
        let bytes = if be {
            e.val.to_be_bytes()
        } else {
            e.val.to_le_bytes()
        };
        return u64::from(num(&bytes, be, false));
    }
    u64::from(e.val)
}

/// 从 IFD 里收集候选 JPEG 预览区间（offset, len）。
fn candidates_in(entries: &[(u16, u32, u64)], out: &mut Vec<(u64, u64)>) {
    let find = |tag: u16| {
        entries
            .iter()
            .find(|(t, _, _)| *t == tag)
            .map(|(_, _, v)| *v)
    };
    // 0x0201/0x0202：JPEGInterchangeFormat(Length)，CR2/ARW/DNG 的缩小图走这里
    if let (Some(off), Some(len)) = (find(0x0201), find(0x0202))
        && len > 128
    {
        out.push((off, len));
    }
    // 0x0111/0x0117：StripOffsets/StripByteCounts，压缩方式 6/7 表示 JPEG
    let compression = find(0x0103).unwrap_or(1);
    if matches!(compression, 6 | 7)
        && let (Some(off), Some(len)) = (find(0x0111), find(0x0117))
        && len > 128
    {
        out.push((off, len));
    }
}

/// TIFF 容器快路径：走 IFD0、IFD1、SubIFD，拿到精确预览区间。
fn tiff_candidates(f: &mut File, be: bool, file_len: u64) -> Vec<(u64, u64)> {
    let mut out = vec![];
    let head = match read_at(f, 0, 8, file_len) {
        Some(h) => h,
        None => return out,
    };
    let mut ifd_offset = u64::from(num(&head[4..8], be, true));
    let mut depth = 0u8;
    let mut visited = vec![];
    while ifd_offset != 0 && visited.len() < 8 {
        if visited.contains(&ifd_offset) {
            break;
        }
        visited.push(ifd_offset);
        let Some((entries, next)) = read_ifd(f, be, ifd_offset, file_len) else {
            break;
        };
        let flat: Vec<(u16, u32, u64)> = entries
            .iter()
            .map(|e| (e.tag, e.count, entry_value(e, be)))
            .collect();
        candidates_in(&flat, &mut out);
        depth = depth.saturating_add(1);
        if depth <= MAX_IFD_DEPTH {
            // SubIFDs(0x014A)：可能是单个偏移，也可能是偏移数组
            if let Some(sub) = entries.iter().find(|e| e.tag == 0x014A) {
                let mut offsets = vec![];
                if sub.count <= 1 {
                    offsets.push(u64::from(sub.val));
                } else if let Some(raw) =
                    read_at(f, u64::from(sub.val), sub.count as usize * 4, file_len)
                {
                    for i in 0..sub.count as usize {
                        offsets.push(u64::from(num(&raw[i * 4..i * 4 + 4], be, true)));
                    }
                }
                for o in offsets {
                    if let Some((sub_entries, _)) = read_ifd(f, be, o, file_len) {
                        let flat: Vec<(u16, u32, u64)> = sub_entries
                            .iter()
                            .map(|e| (e.tag, e.count, entry_value(e, be)))
                            .collect();
                        candidates_in(&flat, &mut out);
                    }
                }
            }
        }
        ifd_offset = next;
    }
    out.retain(|(off, len)| {
        *len <= MAX_PREVIEW && off.saturating_add(*len) <= file_len && *off > 0
    });
    out
}

/// 富士 RAF：头部 84 字节处是大端 JPEG 偏移，88 字节处是长度。
fn raf_candidate(f: &mut File, file_len: u64) -> Option<(u64, u64)> {
    let head = read_at(f, 0, 96, file_len)?;
    let off = u64::from(num(&head[84..88], true, true));
    let len = u64::from(num(&head[88..92], true, true));
    (off > 0 && len > 128).then_some((off, len))
}

/// 通用兜底：流式扫描 SOI…EOI 区间。只记录偏移，不把整个文件读入内存。
fn jpeg_ranges(f: &mut File, limit: u64) -> Vec<(u64, u64)> {
    f.seek(SeekFrom::Start(0)).ok();
    let mut out = vec![];
    let mut buf = vec![0u8; 256 * 1024];
    let mut pos = 0u64;
    let mut start: Option<u64> = None;
    let mut p2 = 0u8;
    let mut p1 = 0u8;
    while pos < limit && out.len() < 64 {
        let want = buf.len().min((limit - pos) as usize);
        let Ok(n) = f.read(&mut buf[..want]) else {
            break;
        };
        if n == 0 {
            break;
        }
        for (i, b) in buf[..n].iter().enumerate() {
            let abs = pos + i as u64;
            if p2 == 0xFF && p1 == 0xD8 && *b == 0xFF {
                start = Some(abs - 2);
            }
            if start.is_some() && p1 == 0xFF && *b == 0xD9 {
                let s = start.take().unwrap();
                let len = abs + 1 - s;
                if len > 128 && len <= MAX_PREVIEW {
                    out.push((s, len));
                }
            }
            p2 = p1;
            p1 = *b;
        }
        pos += n as u64;
    }
    out
}

fn decode_preview(bytes: &[u8]) -> Option<(DynamicImage, u32, u32)> {
    let mut reader = ImageReader::new(Cursor::new(bytes))
        .with_guessed_format()
        .ok()?;
    let mut limits = Limits::default();
    limits.max_image_width = Some(MAX_EDGE);
    limits.max_image_height = Some(MAX_EDGE);
    limits.max_alloc = Some(MAX_ALLOC);
    reader.limits(limits);
    let image = reader.decode().ok()?;
    Some((image.clone(), image.width(), image.height()))
}

fn load_candidate(
    f: &mut File,
    range: (u64, u64),
    file_len: u64,
) -> Option<(Vec<u8>, DynamicImage)> {
    let (offset, len) = range;
    let head = read_at(f, offset, 3, file_len)?;
    if head != [0xFF, 0xD8, 0xFF] {
        return None;
    }
    let bytes = read_at(f, offset, len as usize, file_len)?;
    decode_preview(&bytes).map(|(image, _, _)| (bytes, image))
}

/// 提取内嵌预览。顺序：TIFF IFD → RAF 头 → 通用 JPEG 扫描；每个候选都要真正解码成功才算数。
pub fn extract_preview(file: &mut File, file_len: u64) -> CoreResult<RawPreview> {
    let head = read_at(file, 0, 16, file_len).unwrap_or_default();
    let format = detect_format(&head);
    if format.starts_with("bigtiff") {
        return Err(CoreError::new(
            "RAW_UNSUPPORTED",
            "BigTIFF 容器的 RAW 暂不支持内嵌预览提取",
        ));
    }
    if format == "heif" {
        return Err(CoreError::new(
            "RAW_UNSUPPORTED",
            "HEIF 容器需要独立解码器，当前暂不支持",
        ));
    }
    let mut routes: Vec<(&'static str, Vec<(u64, u64)>)> = vec![];
    if matches!(format, "tiff_le" | "tiff_be") {
        let be = format == "tiff_be";
        let mut c = tiff_candidates(file, be, file_len);
        c.sort_by_key(|r| std::cmp::Reverse(r.1));
        routes.push(("tiff_ifd", c));
    }
    if format == "fujifilm_raf" {
        routes.push((
            "raf_header",
            raf_candidate(file, file_len).into_iter().collect(),
        ));
    }
    let mut scanned = jpeg_ranges(file, SCAN_LIMIT.min(file_len));
    scanned.sort_by_key(|r| std::cmp::Reverse(r.1));
    routes.push(("jpeg_scan", scanned));

    for (source, ranges) in routes {
        for range in ranges.into_iter().take(12) {
            if let Some((bytes, _)) = load_candidate(file, range, file_len) {
                return Ok(RawPreview {
                    format,
                    bytes,
                    offset: range.0,
                    source,
                });
            }
        }
    }
    Err(CoreError::new(
        "RAW_PREVIEW_NOT_FOUND",
        "未在文件中找到可解码的内嵌预览，暂不支持该机型 RAW",
    ))
}

fn rational(v: &exif::Value) -> Option<f64> {
    match v {
        exif::Value::Rational(r) if !r.is_empty() => Some(r[0].to_f64()),
        exif::Value::SRational(r) if !r.is_empty() => Some(r[0].to_f64()),
        _ => None,
    }
}

fn field_text(exif: &exif::Exif, tag: exif::Tag) -> Option<String> {
    let f = exif.get_field(tag, exif::In::PRIMARY)?;
    let s = f.display_value().to_string();
    (!s.trim().is_empty() && s.len() <= 300).then_some(s)
}

/// 只保留选定的拍摄字段。GPS、原始 EXIF 块一律不入库。
pub fn exif_fields(exif: &exif::Exif) -> (Value, u32) {
    let mut info = json!({});
    for (tag, key) in [
        (exif::Tag::Make, "cameraMake"),
        (exif::Tag::Model, "cameraModel"),
        (exif::Tag::LensModel, "lensModel"),
        (exif::Tag::PhotographicSensitivity, "iso"),
        (exif::Tag::FNumber, "fNumber"),
        (exif::Tag::ExposureTime, "exposureTime"),
        (exif::Tag::FocalLength, "focalLength"),
    ] {
        let value = if matches!(tag, exif::Tag::FNumber | exif::Tag::ExposureTime) {
            exif.get_field(tag, exif::In::PRIMARY)
                .and_then(|f| rational(&f.value))
                .map(|v| json!(v))
        } else {
            field_text(exif, tag).map(|v| json!(v))
        };
        if let Some(v) = value {
            info[key] = v;
        }
    }
    for tag in [
        exif::Tag::DateTimeOriginal,
        exif::Tag::DateTimeDigitized,
        exif::Tag::DateTime,
    ] {
        if let Some(text) = field_text(exif, tag) {
            info["capturedAt"] = json!(text);
            info["timestampSource"] = json!(match tag {
                exif::Tag::DateTimeOriginal => "exif_DateTimeOriginal",
                exif::Tag::DateTimeDigitized => "exif_DateTimeDigitized",
                _ => "exif_DateTime",
            });
            break;
        }
    }
    let orientation = exif
        .get_field(exif::Tag::Orientation, exif::In::PRIMARY)
        .and_then(|f| f.value.get_uint(0))
        .unwrap_or(1);
    (info, orientation)
}

fn orient(image: DynamicImage, n: u32) -> DynamicImage {
    match n {
        2 => image.fliph(),
        3 => image.rotate180(),
        4 => image.flipv(),
        5 => image.rotate90().fliph(),
        6 => image.rotate90(),
        7 => image.rotate270().fliph(),
        8 => image.rotate270(),
        _ => image,
    }
}

/// 曝光/清晰度的本地信号。与照片链路同一套命名，保证 UI 与报告口径一致。
pub fn image_signals(image: &DynamicImage) -> (Value, &'static str, Vec<u8>) {
    let (w, h) = (image.width(), image.height());
    let small = image.thumbnail(512, 512);
    let gray = small.to_luma8();
    let n = gray.len().max(1) as f64;
    let highlights = gray.iter().filter(|p| **p >= 250).count() as f64 / n;
    let shadows = gray.iter().filter(|p| **p <= 5).count() as f64 / n;
    let mut sum = 0f64;
    let mut sq = 0f64;
    let mut count = 0f64;
    if gray.width() > 2 && gray.height() > 2 {
        for y in 1..gray.height() - 1 {
            for x in 1..gray.width() - 1 {
                let at = |x, y| gray.get_pixel(x, y).0[0] as f64;
                let lap =
                    4.0 * at(x, y) - at(x - 1, y) - at(x + 1, y) - at(x, y - 1) - at(x, y + 1);
                sum += lap;
                sq += lap * lap;
                count += 1.0;
            }
        }
    }
    let variance = if count > 0.0 {
        (sq / count - (sum / count).powi(2)).max(0.0)
    } else {
        0.0
    };
    let mut candidates = vec![];
    if highlights > 0.15 {
        candidates.push("highlight_clipping_candidate");
    }
    if shadows > 0.50 {
        candidates.push("deep_shadow_candidate");
    }
    if variance < 35.0 {
        candidates.push("low_detail_candidate");
    }
    let state = if candidates.is_empty() {
        "unanalyzed"
    } else {
        "review"
    };
    let mut encoded = vec![];
    let _ = JpegEncoder::new_with_quality(&mut encoded, 82)
        .encode_image(&DynamicImage::ImageRgb8(small.to_rgb8()));
    (
        json!({"status":"needs_human_review","model":"local-signals-v1","calibrated":false,"confidence":null,"width":w,"height":h,"highlightRatio":highlights,"shadowRatio":shadows,"laplacianVarianceAt512":variance,"candidates":candidates,"automaticCleanupAllowed":false}),
        state,
        encoded,
    )
}

impl Engine {
    /// 分析一张 RAW：提取内嵌预览 → 精选 EXIF → 512px 缩略图 → 写库。
    /// 原片不会被修改；入口与出口各核一次 fingerprint。
    pub fn analyze_raw(&self, media_id: &str) -> CoreResult<Value> {
        let c = self.store.connect()?;
        let (path, kind, metadata): (String, String, String) = c.query_row(
            "SELECT path,kind,metadata FROM media_items WHERE id=?1",
            [media_id],
            |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)),
        )?;
        if kind != "photo" {
            return Err(CoreError::new("PHOTO_REQUIRED", "该接口只分析照片类文件"));
        }
        let path = PathBuf::from(path);
        if !is_raw_path(&path) {
            return Err(CoreError::new(
                "RAW_REQUIRED",
                "该文件不是 RAW 格式，请使用照片分析接口",
            ));
        }
        let root = SafeDir::open(
            path.parent()
                .ok_or_else(|| CoreError::new("UNSAFE_PATH", "缺父目录"))?,
        )?;
        let relative = Path::new(path.file_name().unwrap());
        let mut file = root.read(relative)?;
        let before = fsops::fingerprint(&file.metadata()?);
        let mut info: Value = serde_json::from_str(&metadata)?;
        if info
            .get("fingerprint")
            .is_some_and(|fp| *fp != json!(before))
        {
            return Err(CoreError::new(
                "SOURCE_CHANGED",
                "索引后来源发生变化，请重新扫描",
            ));
        }
        let file_len = before.bytes;
        let preview = extract_preview(&mut file, file_len)?;
        let (image, width, height) = decode_preview(&preview.bytes)
            .ok_or_else(|| CoreError::new("RAW_PREVIEW_DECODE_FAILED", "内嵌预览解码失败"))?;

        // EXIF：先读 RAW 容器自身（TIFF 系可直接读），失败再读内嵌预览
        file.seek(SeekFrom::Start(0))?;
        let exif = exif::Reader::new()
            .read_from_container(&mut BufReader::new(&mut file))
            .ok()
            .or_else(|| {
                exif::Reader::new()
                    .read_from_container(&mut BufReader::new(Cursor::new(&preview.bytes)))
                    .ok()
            });
        let mut orientation = 1;
        if let Some(exif) = &exif {
            let (fields, o) = exif_fields(exif);
            orientation = o;
            if let Some(map) = fields.as_object() {
                for (k, v) in map {
                    info[k] = v.clone();
                }
            }
            info["exifStatus"] = json!("selected_fields_read");
        } else {
            info["exifStatus"] = json!("unavailable");
        }
        if info.get("timestampSource").is_none() {
            info["timestampSource"] = json!("filesystem_modified");
        }

        let (quality, mut state, thumb) = image_signals(&orient(image, orientation));
        info["analyzerVersion"] = json!("local-signals-v1");
        info["rawFormat"] = json!(preview.format);
        info["previewSource"] = json!(preview.source);
        info["previewOffset"] = json!(preview.offset);
        info["previewBytes"] = json!(preview.bytes.len());
        info["previewWidth"] = json!(width);
        info["previewHeight"] = json!(height);
        info["decodeStatus"] = json!("raw_preview_decoded");
        info["codec"] = json!("embedded_jpeg_preview");
        info["fullDecode"] = json!(false);
        info["quality"] = quality;
        if info
            .get("humanReview")
            .and_then(|r| r.get("state"))
            .and_then(Value::as_str)
            .is_some_and(|r| ["good", "review", "unanalyzed"].contains(&r))
        {
            state = info["humanReview"]["state"].as_str().unwrap_or(state);
        }
        // 只读复核：出口再核一次，任何变化都不保存分析
        if fsops::fingerprint(&root.read(relative)?.metadata()?) != before {
            return Err(CoreError::new(
                "SOURCE_CHANGED",
                "分析期间来源发生变化，未保存分析",
            ));
        }
        let mut thumbnail_path = None;
        if !thumb.is_empty() {
            let name = PathBuf::from("thumbnails").join(format!("{}-{}.jpg", media_id, id()));
            fsops::write_new(&SafeDir::open(&self.store.root)?, &name, &thumb)?;
            thumbnail_path = Some(name);
        }
        let mut conn = self.store.connect()?;
        let tx = conn.transaction_with_behavior(rusqlite::TransactionBehavior::Immediate)?;
        tx.execute(
            "UPDATE media_items SET metadata=?1,quality_state=?2 WHERE id=?3",
            rusqlite::params![info.to_string(), state, media_id],
        )?;
        if let Some(p) = &thumbnail_path {
            tx.execute("INSERT INTO media_derivatives(media_id,path,source_fingerprint,created_at) VALUES(?1,?2,?3,?4) ON CONFLICT(media_id) DO UPDATE SET path=excluded.path,source_fingerprint=excluded.source_fingerprint,created_at=excluded.created_at",rusqlite::params![media_id,p.to_string_lossy(),serde_json::to_string(&before)?,now()])?;
        } else {
            tx.execute(
                "DELETE FROM media_derivatives WHERE media_id=?1",
                [media_id],
            )?;
        }
        tx.commit()?;
        Ok(json!({
            "mediaId":media_id,
            "qualityState":state,
            "rawFormat":preview.format,
            "previewSource":preview.source,
            "thumbnailAvailable":thumbnail_path.is_some(),
            "metadata":info,
            "sourceReadOnly":true,
            "fullDecodePerformed":false
        }))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use image::{Rgb, RgbImage};
    use std::io::Write;

    fn jpeg(w: u32, h: u32) -> Vec<u8> {
        let mut img = RgbImage::new(w, h);
        for (x, y, p) in img.enumerate_pixels_mut() {
            *p = Rgb([(x % 255) as u8, (y % 255) as u8, 90]);
        }
        let mut out = vec![];
        JpegEncoder::new_with_quality(&mut out, 80)
            .encode_image(&DynamicImage::ImageRgb8(img))
            .unwrap();
        out
    }

    /// 构造一个最小 TIFF：IFD0 指向 IFD1，IFD1 用 0x0201/0x0202 指向内嵌 JPEG。
    fn tiff_with_preview(preview: &[u8]) -> Vec<u8> {
        let ifd0 = 8u32;
        let ifd1 = ifd0 + 2 + 12 + 4; // IFD0 只有 1 个条目
        let data = ifd1 + 2 + 2 * 12 + 4; // IFD1 有 2 个条目
        let mut out = Vec::new();
        out.extend_from_slice(b"II\x2a\x00");
        out.extend_from_slice(&ifd0.to_le_bytes());
        // IFD0：一个条目 0x0103(Compression)=1，next 指向 ifd1
        out.extend_from_slice(&1u16.to_le_bytes());
        out.extend_from_slice(&0x0103u16.to_le_bytes());
        out.extend_from_slice(&3u16.to_le_bytes());
        out.extend_from_slice(&1u32.to_le_bytes());
        out.extend_from_slice(&[1, 0, 0, 0]);
        out.extend_from_slice(&ifd1.to_le_bytes());
        // IFD1：0x0201 偏移 + 0x0202 长度
        out.extend_from_slice(&2u16.to_le_bytes());
        out.extend_from_slice(&0x0201u16.to_le_bytes());
        out.extend_from_slice(&4u16.to_le_bytes());
        out.extend_from_slice(&1u32.to_le_bytes());
        out.extend_from_slice(&data.to_le_bytes());
        out.extend_from_slice(&0x0202u16.to_le_bytes());
        out.extend_from_slice(&4u16.to_le_bytes());
        out.extend_from_slice(&1u32.to_le_bytes());
        out.extend_from_slice(&(preview.len() as u32).to_le_bytes());
        out.extend_from_slice(&0u32.to_le_bytes());
        assert_eq!(out.len(), data as usize);
        out.extend_from_slice(preview);
        out
    }

    /// macOS 上 /var 是符号链接，必须用 canonical 路径，否则 safe-dir 检查会拒绝。
    fn base() -> PathBuf {
        std::env::temp_dir().canonicalize().unwrap()
    }

    fn tmp(name: &str, bytes: &[u8]) -> PathBuf {
        let dir = base().join(format!("yingxu-raw-{}", id()));
        std::fs::create_dir_all(&dir).unwrap();
        let p = dir.join(name);
        let mut f = std::fs::File::create(&p).unwrap();
        f.write_all(bytes).unwrap();
        p
    }

    #[test]
    fn tiff_ifd_preview_is_extracted() {
        let preview = jpeg(640, 480);
        let raw = tiff_with_preview(&preview);
        let p = tmp("sample.cr2", &raw);
        let mut f = std::fs::File::open(&p).unwrap();
        let len = f.metadata().unwrap().len();
        let found = extract_preview(&mut f, len).unwrap();
        assert_eq!(found.source, "tiff_ifd");
        assert_eq!(found.format, "tiff_le");
        assert_eq!(found.bytes.len(), preview.len());
    }

    #[test]
    fn jpeg_scan_fallback_finds_cr3_style_preview() {
        let preview = jpeg(320, 240);
        let mut raw = Vec::new();
        raw.extend_from_slice(&20u32.to_be_bytes());
        raw.extend_from_slice(b"ftypcrx ");
        raw.extend_from_slice(&[0u8; 128]);
        raw.extend_from_slice(&preview);
        raw.extend_from_slice(&[0u8; 512]);
        let p = tmp("sample.cr3", &raw);
        let mut f = std::fs::File::open(&p).unwrap();
        let len = f.metadata().unwrap().len();
        let found = extract_preview(&mut f, len).unwrap();
        assert_eq!(found.format, "cr3");
        assert_eq!(found.source, "jpeg_scan");
        assert_eq!(found.bytes, preview);
    }

    #[test]
    fn raf_header_offset_is_used() {
        let preview = jpeg(160, 120);
        let mut raw = vec![0u8; 96];
        raw[0..16].copy_from_slice(b"FUJIFILMCCD-RAW ");
        let data_at = 96u32;
        raw[84..88].copy_from_slice(&data_at.to_be_bytes());
        raw[88..92].copy_from_slice(&(preview.len() as u32).to_be_bytes());
        raw.extend_from_slice(&preview);
        let p = tmp("sample.raf", &raw);
        let mut f = std::fs::File::open(&p).unwrap();
        let len = f.metadata().unwrap().len();
        let found = extract_preview(&mut f, len).unwrap();
        assert_eq!(found.format, "fujifilm_raf");
        assert_eq!(found.source, "raf_header");
    }

    #[test]
    fn files_without_preview_report_explicit_error() {
        let p = tmp("broken.nef", &[0u8; 4096]);
        let mut f = std::fs::File::open(&p).unwrap();
        let len = f.metadata().unwrap().len();
        let err = extract_preview(&mut f, len).unwrap_err();
        assert_eq!(err.code, "RAW_PREVIEW_NOT_FOUND");
        let mut bigtiff = vec![0u8; 64];
        bigtiff[0..4].copy_from_slice(&[b'I', b'I', 0x2b, 0]);
        let p = tmp("old.arw", &bigtiff);
        let mut f = std::fs::File::open(&p).unwrap();
        let len = f.metadata().unwrap().len();
        assert_eq!(
            extract_preview(&mut f, len).unwrap_err().code,
            "RAW_UNSUPPORTED"
        );
    }

    #[test]
    fn analyze_raw_end_to_end_keeps_source_bytes() {
        let preview = jpeg(800, 600);
        let raw = tiff_with_preview(&preview);
        let dir = base().join(format!("yingxu-raw-e2e-{}", id()));
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(dir.join("shot.cr2"), &raw).unwrap();
        let engine = Engine::open(base().join(format!("yingxu-raw-store-{}", id()))).unwrap();
        engine.index_source(&dir).unwrap();
        let media_id: String = engine
            .store
            .connect()
            .unwrap()
            .query_row(
                "SELECT id FROM media_items WHERE name='shot.cr2'",
                [],
                |r| r.get(0),
            )
            .unwrap();
        let result = engine.analyze_raw(&media_id).unwrap();
        assert_eq!(result["rawFormat"], "tiff_le");
        assert_eq!(result["sourceReadOnly"], true);
        assert_eq!(std::fs::read(dir.join("shot.cr2")).unwrap(), raw);
        let thumb = engine.thumbnail(&media_id).unwrap();
        assert!(thumb["bytes"].as_array().is_some_and(|b| !b.is_empty()));
    }

    #[test]
    fn non_raw_file_is_rejected() {
        let dir = base().join(format!("yingxu-raw-reject-{}", id()));
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(dir.join("a.jpg"), jpeg(64, 64)).unwrap();
        let engine = Engine::open(base().join(format!("yingxu-raw-store2-{}", id()))).unwrap();
        engine.index_source(&dir).unwrap();
        let media_id: String = engine
            .store
            .connect()
            .unwrap()
            .query_row("SELECT id FROM media_items WHERE name='a.jpg'", [], |r| {
                r.get(0)
            })
            .unwrap();
        assert_eq!(
            engine.analyze_raw(&media_id).unwrap_err().code,
            "RAW_REQUIRED"
        );
    }
}
