//! Local-only metadata and bounded image analysis. Heuristics are review hints, never deletion rules.
use crate::{
    Engine,
    fsops::{self, SafeDir},
    model::*,
};
use image::{DynamicImage, ImageReader, Limits, codecs::jpeg::JpegEncoder};
use rusqlite::{OptionalExtension, params};
use serde_json::{Value, json};
use std::{
    io::{BufReader, Cursor, Read},
    path::{Path, PathBuf},
};
const MAX_INPUT: u64 = 128 * 1024 * 1024;
impl Engine {
    pub fn index_source(&self, source: &Path) -> CoreResult<Value> {
        let source = fsops::local_directory(source)?;
        if source.starts_with(&self.store.root) || self.store.root.starts_with(&source) {
            return Err(CoreError::new("APP_DATA_OVERLAP", "不能索引应用数据目录"));
        }
        let files = fsops::scan(&source)?;
        let mut c = self.store.connect()?;
        let tx = c.transaction_with_behavior(rusqlite::TransactionBehavior::Immediate)?;
        let mut changed = 0;
        for f in &files {
            let path = source.join(&f.relative);
            let path = path.to_str().ok_or_else(|| {
                CoreError::new("INVALID_PATH_ENCODING", "非 UTF-8 文件名暂不支持索引")
            })?;
            let old: Option<(String, String)> = tx
                .query_row(
                    "SELECT id,metadata FROM media_items WHERE path=?1",
                    [path],
                    |r| Ok((r.get(0)?, r.get(1)?)),
                )
                .optional()?;
            if old.as_ref().is_some_and(|(_, m)| {
                serde_json::from_str::<Value>(m)
                    .is_ok_and(|v| v["fingerprint"] == json!(f.fingerprint))
            }) {
                continue;
            }
            changed += 1;
            let key = old.map(|(key, _)| key).unwrap_or_else(id);
            let metadata = json!({"fingerprint":f.fingerprint,"timestampSource":"filesystem_modified","exifStatus":"not_analyzed"});
            let name = f.relative.file_name().unwrap_or_default().to_string_lossy();
            let bytes = i64::try_from(f.fingerprint.bytes)
                .map_err(|_| CoreError::new("FILE_TOO_LARGE", path))?;
            tx.execute("INSERT INTO media_items(id,path,name,kind,bytes,metadata) VALUES(?1,?2,?3,?4,?5,?6) ON CONFLICT(path) DO UPDATE SET name=excluded.name,kind=excluded.kind,bytes=excluded.bytes,metadata=excluded.metadata,sha256=NULL,quality_state='unanalyzed'",params![key,path,name,f.kind,bytes,metadata.to_string()])?;
            tx.execute("DELETE FROM media_derivatives WHERE media_id=?1", [key])?;
        }
        tx.commit()?;
        Ok(json!({"files":files.len(),"changed":changed,"sourceReadOnly":true,"copyStarted":false}))
    }
    pub fn analyze_photo(&self, media_id: &str) -> CoreResult<Value> {
        let c = self.store.connect()?;
        let (path, kind, metadata): (String, String, String) = c.query_row(
            "SELECT path,kind,metadata FROM media_items WHERE id=?1",
            [media_id],
            |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)),
        )?;
        if kind != "photo" {
            return Err(CoreError::new(
                "PHOTO_REQUIRED",
                "该接口只分析照片；视频需要独立抽帧流程",
            ));
        }
        let path = PathBuf::from(path);
        let root = SafeDir::open(
            path.parent()
                .ok_or_else(|| CoreError::new("UNSAFE_PATH", "缺父目录"))?,
        )?;
        let relative = Path::new(path.file_name().unwrap());
        let file = root.read(relative)?;
        let before = fsops::fingerprint(&file.metadata()?);
        let old: Value = serde_json::from_str(&metadata)?;
        if old
            .get("fingerprint")
            .is_some_and(|fp| *fp != json!(before))
        {
            return Err(CoreError::new(
                "SOURCE_CHANGED",
                "索引后来源发生变化，请重新扫描",
            ));
        }
        if before.bytes > MAX_INPUT {
            return Err(CoreError::new(
                "DECODE_LIMIT",
                "单图编码数据超过 128 MiB，本轮不解码",
            ));
        }
        let mut bytes = vec![];
        file.take(MAX_INPUT + 1).read_to_end(&mut bytes)?;
        if bytes.len() as u64 > MAX_INPUT {
            return Err(CoreError::new("DECODE_LIMIT", "图像读取超过限制"));
        }
        let mut info = old;
        info["analyzerVersion"] = json!("local-signals-v1");
        info["timestampSource"] = json!("filesystem_modified");
        let exif = exif::Reader::new()
            .read_from_container(&mut BufReader::new(Cursor::new(&bytes)))
            .ok();
        let mut orientation = 1;
        if let Some(exif) = &exif {
            for (tag, key) in [
                (exif::Tag::Make, "cameraMake"),
                (exif::Tag::Model, "cameraModel"),
                (exif::Tag::DateTimeOriginal, "capturedAt"),
            ] {
                if let Some(field) = exif.get_field(tag, exif::In::PRIMARY) {
                    let text = field.display_value().to_string();
                    if text.len() <= 500 {
                        info[key] = json!(text);
                        if key == "capturedAt" {
                            info["timestampSource"] = json!("exif_DateTimeOriginal");
                        }
                    }
                }
            }
            orientation = exif
                .get_field(exif::Tag::Orientation, exif::In::PRIMARY)
                .and_then(|f| f.value.get_uint(0))
                .unwrap_or(1);
            info["exifStatus"] = json!("selected_fields_read");
        } else {
            info["exifStatus"] = json!("unavailable");
        }
        // Only fields above are retained: exact GPS and raw EXIF blocks never enter this record.
        let decoded = (|| -> Result<DynamicImage, image::ImageError> {
            let mut reader = ImageReader::new(Cursor::new(&bytes)).with_guessed_format()?;
            let mut limits = Limits::default();
            limits.max_image_width = Some(16384);
            limits.max_image_height = Some(16384);
            limits.max_alloc = Some(256 * 1024 * 1024);
            reader.limits(limits);
            reader.decode()
        })();
        let mut thumb = None;
        let mut state = "unanalyzed";
        match decoded {
            Ok(image) => {
                let (w, h) = (image.width(), image.height());
                info["width"] = json!(w);
                info["height"] = json!(h);
                let small = orient(image.thumbnail(512, 512), orientation);
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
                            let lap = 4.0 * at(x, y)
                                - at(x - 1, y)
                                - at(x + 1, y)
                                - at(x, y - 1)
                                - at(x, y + 1);
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
                if !candidates.is_empty() {
                    state = "review";
                }
                info["decodeStatus"] = json!("decoded");
                info["quality"] = json!({"status":"needs_human_review","model":"local-signals-v1","calibrated":false,"confidence":null,"highlightRatio":highlights,"shadowRatio":shadows,"laplacianVarianceAt512":variance,"candidates":candidates,"automaticCleanupAllowed":false,"musiq":"not_installed"});
                let mut encoded = vec![];
                JpegEncoder::new_with_quality(&mut encoded, 82)
                    .encode_image(&DynamicImage::ImageRgb8(small.to_rgb8()))
                    .map_err(|e| CoreError::new("THUMBNAIL_ENCODE_FAILED", e))?;
                thumb = Some(encoded);
            }
            Err(error) => {
                info["decodeStatus"] = json!(if matches!(error, image::ImageError::Limits(_)) {
                    "resource_limit"
                } else {
                    "unsupported_or_decode_failed"
                });
                info["decodeReason"] = json!(error.to_string());
                info["quality"] = json!({"status":"unanalyzed","automaticCleanupAllowed":false});
            }
        }
        if fsops::fingerprint(&root.read(relative)?.metadata()?) != before {
            return Err(CoreError::new(
                "SOURCE_CHANGED",
                "分析期间来源发生变化，未保存分析",
            ));
        }
        let mut thumbnail_path = None;
        if let Some(bytes) = thumb {
            let name = PathBuf::from("thumbnails").join(format!("{}-{}.jpg", media_id, id()));
            fsops::write_new(&SafeDir::open(&self.store.root)?, &name, &bytes)?;
            thumbnail_path = Some(name);
        }
        if let Some(review) = info
            .get("humanReview")
            .and_then(|r| r.get("state"))
            .and_then(Value::as_str)
            && ["good", "review", "unanalyzed"].contains(&review)
        {
            state = review;
        }
        let mut conn = self.store.connect()?;
        let tx = conn.transaction_with_behavior(rusqlite::TransactionBehavior::Immediate)?;
        tx.execute(
            "UPDATE media_items SET metadata=?1,quality_state=?2 WHERE id=?3",
            params![info.to_string(), state, media_id],
        )?;
        if let Some(path) = &thumbnail_path {
            tx.execute("INSERT INTO media_derivatives(media_id,path,source_fingerprint,created_at) VALUES(?1,?2,?3,?4) ON CONFLICT(media_id) DO UPDATE SET path=excluded.path,source_fingerprint=excluded.source_fingerprint,created_at=excluded.created_at",params![media_id,path.to_string_lossy(),serde_json::to_string(&before)?,now()])?;
        } else {
            tx.execute(
                "DELETE FROM media_derivatives WHERE media_id=?1",
                [media_id],
            )?;
        }
        tx.commit()?;
        Ok(
            json!({"mediaId":media_id,"qualityState":state,"metadata":info,"thumbnailAvailable":thumbnail_path.is_some()}),
        )
    }
    pub fn thumbnail(&self, media_id: &str) -> CoreResult<Value> {
        let c = self.store.connect()?;
        let (path,expected,source):(String,String,String)=c.query_row("SELECT d.path,d.source_fingerprint,m.path FROM media_derivatives d JOIN media_items m ON m.id=d.media_id WHERE d.media_id=?1",[media_id],|r|Ok((r.get(0)?,r.get(1)?,r.get(2)?)))?;
        let source = Path::new(&source);
        let fp: Fingerprint = serde_json::from_str(&expected)?;
        let dir = SafeDir::open(source.parent().unwrap())?;
        if fsops::fingerprint(
            &dir.read(Path::new(source.file_name().unwrap()))?
                .metadata()?,
        ) != fp
        {
            return Err(CoreError::new("STALE_THUMBNAIL", "来源变化，请重新分析"));
        }
        let file = SafeDir::open(&self.store.root)?.read(Path::new(&path))?;
        let mut bytes = vec![];
        file.take(1024 * 1024 + 1).read_to_end(&mut bytes)?;
        if bytes.len() > 1024 * 1024 {
            return Err(CoreError::new("THUMBNAIL_TOO_LARGE", "缩略图超过限制"));
        }
        Ok(
            json!({"mediaId":media_id,"mimeType":"image/jpeg","bytes":bytes,"exifIncluded":false,"maxEdge":512}),
        )
    }
    pub fn review_media(
        &self,
        media_id: &str,
        state: &str,
        note: &str,
        confirmed: bool,
    ) -> CoreResult<Value> {
        if !confirmed {
            return Err(CoreError::new(
                "CONFIRMATION_REQUIRED",
                "请确认人工复核结果",
            ));
        }
        if !["good", "review", "unanalyzed"].contains(&state) || note.len() > 2000 {
            return Err(CoreError::new("INVALID_REVIEW", "复核状态或备注无效"));
        }
        let mut c = self.store.connect()?;
        let tx = c.transaction_with_behavior(rusqlite::TransactionBehavior::Immediate)?;
        let metadata: String = tx.query_row(
            "SELECT metadata FROM media_items WHERE id=?1",
            [media_id],
            |r| r.get(0),
        )?;
        let mut metadata: Value = serde_json::from_str(&metadata)?;
        metadata["humanReview"] = json!({"state":state,"note":note,"reviewedAt":now()});
        tx.execute(
            "UPDATE media_items SET quality_state=?1,metadata=?2 WHERE id=?3",
            params![state, metadata.to_string(), media_id],
        )?;
        tx.commit()?;
        self.store
            .audit("review_media", media_id, json!({"state":state}))?;
        Ok(json!({"mediaId":media_id,"qualityState":state}))
    }
    pub fn duplicate_candidates(&self) -> CoreResult<Vec<Value>> {
        let c = self.store.connect()?;
        let mut q=c.prepare("SELECT sha256,group_concat(id),count(*) FROM media_items WHERE sha256 IS NOT NULL GROUP BY sha256 HAVING count(*)>1 LIMIT 500")?;
        q.query_map([],|r|Ok(json!({"sha256":r.get::<_,String>(0)?,"mediaIds":r.get::<_,String>(1)?.split(',').collect::<Vec<_>>(),"count":r.get::<_,i64>(2)?,"requiresFreshVerification":true,"automaticCleanupAllowed":false})))?.map(|r|Ok(r?)).collect()
    }
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
