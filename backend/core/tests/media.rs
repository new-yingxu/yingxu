use serde_json::{Value, json};
use std::fs;
use yingxu_core::{Engine, PlanRequest, api::Request};
struct F {
    _tmp: tempfile::TempDir,
    e: Engine,
    source: std::path::PathBuf,
    target: std::path::PathBuf,
}
impl F {
    fn new() -> Self {
        let tmp = tempfile::tempdir().unwrap();
        let root = tmp.path().canonicalize().unwrap();
        let source = root.join("source");
        let target = root.join("target");
        fs::create_dir(&source).unwrap();
        fs::create_dir(&target).unwrap();
        let e = Engine::open(root.join("db")).unwrap();
        Self {
            _tmp: tmp,
            e,
            source,
            target,
        }
    }
    fn import(&self) -> String {
        let p = self
            .e
            .preview(PlanRequest {
                source: self.source.clone(),
                destinations: vec![self.target.clone()],
                name: "test".into(),
                verify: true,
                transfer: false,
                selected: vec![],
                subdirectory: None,
            })
            .unwrap();
        self.e
            .start(&p.id, true, &yingxu_core::model::id())
            .unwrap();
        self.e.worker().unwrap().run_next().unwrap();
        self.e.search("", "all", "all").unwrap()[0]["id"]
            .as_str()
            .unwrap()
            .to_string()
    }
}
#[test]
fn bounded_photo_thumbnail_signals_and_human_review() {
    let f = F::new();
    image::RgbImage::from_pixel(1200, 800, image::Rgb([255, 255, 255]))
        .save(f.source.join("white.png"))
        .unwrap();
    let id = f.import();
    let result = f.e.analyze_photo(&id).unwrap();
    assert_eq!(result["qualityState"], "review");
    assert_eq!(result["metadata"]["quality"]["calibrated"], false);
    assert_eq!(
        result["metadata"]["quality"]["automaticCleanupAllowed"],
        false
    );
    assert_eq!(result["metadata"]["timestampSource"], "filesystem_modified");
    let thumb = f.e.thumbnail(&id).unwrap();
    let bytes: Vec<u8> = serde_json::from_value(thumb["bytes"].clone()).unwrap();
    let image = image::load_from_memory(&bytes).unwrap();
    assert!(image.width() <= 512 && image.height() <= 512);
    assert!(
        exif::Reader::new()
            .read_from_container(&mut std::io::Cursor::new(bytes))
            .is_err()
    );
    assert_eq!(
        f.e.review_media(&id, "good", "人工核对", false)
            .unwrap_err()
            .code,
        "CONFIRMATION_REQUIRED"
    );
    f.e.review_media(&id, "good", "人工核对", true).unwrap();
    assert_eq!(f.e.search("人工核对", "photo", "good").unwrap().len(), 1);
    assert_eq!(f.e.analyze_photo(&id).unwrap()["qualityState"], "good");
    assert!(f.source.join("white.png").is_file());
}
#[test]
fn undecodable_raw_is_retained_and_not_fake_good() {
    let f = F::new();
    fs::write(f.source.join("a.ARW"), b"opaque undecodable RAW").unwrap();
    let id = f.import();
    let result = f.e.analyze_photo(&id).unwrap();
    assert_eq!(result["qualityState"], "unanalyzed");
    assert_eq!(result["thumbnailAvailable"], false);
    assert_eq!(
        result["metadata"]["decodeStatus"],
        "unsupported_or_decode_failed"
    );
    assert_eq!(
        fs::read(f.target.join("a.ARW")).unwrap(),
        b"opaque undecodable RAW"
    );
}
#[test]
fn changed_source_invalidates_thumbnail_and_analysis() {
    let f = F::new();
    image::RgbImage::from_pixel(8, 8, image::Rgb([80, 100, 120]))
        .save(f.source.join("a.png"))
        .unwrap();
    let id = f.import();
    f.e.analyze_photo(&id).unwrap();
    fs::write(f.source.join("a.png"), b"changed").unwrap();
    assert_eq!(f.e.thumbnail(&id).unwrap_err().code, "STALE_THUMBNAIL");
    assert_eq!(f.e.analyze_photo(&id).unwrap_err().code, "SOURCE_CHANGED");
}
#[test]
fn queued_path_payload_respects_current_privacy_switch() {
    let f = F::new();
    f.e.update_settings(json!({"syncPaths":true})).unwrap();
    fs::write(f.source.join("a"), b"a").unwrap();
    f.import();
    let request = || Request {
        request_id: "outbox".into(),
        version: 1,
        method: "sync.outbox".into(),
        params: json!({"afterSequence":0,"limit":20}),
    };
    let first = f.e.dispatch(request()).data.unwrap();
    assert!(first[0]["payload"]["source"].is_string());
    f.e.update_settings(json!({"syncPaths":false})).unwrap();
    let next = f.e.dispatch(request()).data.unwrap();
    assert_eq!(next[0]["payload"].get("source"), None);
    assert_ne!(next[0]["eventId"], first[0]["eventId"]);
    assert!(next[0]["sequence"].as_i64().unwrap() > first[0]["sequence"].as_i64().unwrap());
    f.e.update_settings(json!({"syncHistory":false})).unwrap();
    assert_eq!(f.e.dispatch(request()).data, Some(Value::Array(vec![])));
}

#[test]
fn source_index_is_read_only_and_preserves_current_analysis() {
    let f = F::new();
    image::RgbImage::from_pixel(12, 8, image::Rgb([80, 100, 120]))
        .save(f.source.join("a.png"))
        .unwrap();
    let before = fs::read(f.source.join("a.png")).unwrap();
    let indexed = f.e.index_source(&f.source).unwrap();
    assert_eq!(indexed["files"], 1);
    assert_eq!(indexed["copyStarted"], false);
    assert!(f.e.store.jobs().unwrap().is_empty());
    let id = f.e.search("", "all", "all").unwrap()[0]["id"]
        .as_str()
        .unwrap()
        .to_string();
    f.e.analyze_photo(&id).unwrap();
    assert_eq!(f.e.index_source(&f.source).unwrap()["changed"], 0);
    assert!(f.e.thumbnail(&id).is_ok());
    assert_eq!(fs::read(f.source.join("a.png")).unwrap(), before);
}

#[test]
fn oversized_dimensions_are_unanalyzed_not_damaged() {
    let f = F::new();
    image::RgbImage::from_pixel(20000, 1, image::Rgb([80, 100, 120]))
        .save(f.source.join("wide.png"))
        .unwrap();
    let id = f.import();
    let response = f.e.analyze_photo(&id).unwrap();
    assert_eq!(response["qualityState"], "unanalyzed");
    assert_eq!(response["metadata"]["decodeStatus"], "resource_limit");
    assert_eq!(response["thumbnailAvailable"], false);
    assert!(f.target.join("wide.png").is_file());
}

#[test]
fn library_search_page_covers_501_rows_and_applies_filters_from_zero() {
    let f = F::new();
    {
        let c = f.e.store.connect().unwrap();
        for i in 0..501 {
            let kind = if i % 2 == 0 { "photo" } else { "video" };
            let quality = if i % 3 == 0 { "review" } else { "good" };
            c.execute(
                "INSERT INTO media_items(id,path,name,kind,bytes,quality_state,metadata) VALUES(?1,?2,?3,?4,1,?5,'{}')",
                rusqlite::params![
                    format!("media-{i:04}"),
                    format!("/fixture/{i:04}.jpg"),
                    format!("fixture-{i:04}"),
                    kind,
                    quality,
                ],
            )
            .unwrap();
        }
    }

    let page = |query: &str, kind: &str, quality: &str, offset| {
        f.e.dispatch(Request {
            request_id: "page".into(),
            version: 1,
            method: "library.searchPage".into(),
            params: json!({
                "query": query,
                "kind": kind,
                "quality": quality,
                "limit": 200,
                "offset": offset,
            }),
        })
    };

    let first = page("", "all", "all", 0).data.unwrap();
    let second = page("", "all", "all", 200).data.unwrap();
    let third = page("", "all", "all", 400).data.unwrap();
    assert_eq!(first["items"].as_array().unwrap().len(), 200);
    assert_eq!(second["items"].as_array().unwrap().len(), 200);
    assert_eq!(third["items"].as_array().unwrap().len(), 101);
    assert_eq!(first["total"], 501);
    assert_eq!(first["offset"], 0);
    assert_eq!(third["offset"], 400);
    assert_eq!(first["hasMore"], true);
    assert_eq!(second["hasMore"], true);
    assert_eq!(third["hasMore"], false);
    let ids: Vec<_> = [first, second, third]
        .into_iter()
        .flat_map(|p| {
            p["items"]
                .as_array()
                .unwrap()
                .iter()
                .map(|row| row["id"].as_str().unwrap().to_string())
                .collect::<Vec<_>>()
        })
        .collect();
    assert_eq!(ids.len(), 501);
    assert_eq!(
        ids.iter().collect::<std::collections::HashSet<_>>().len(),
        501
    );

    let filtered = page("fixture-0500", "photo", "good", 0).data.unwrap();
    assert_eq!(filtered["offset"], 0);
    assert_eq!(filtered["total"], 1);
    assert_eq!(filtered["items"][0]["id"], "media-0500");
    assert_eq!(filtered["hasMore"], false);

    let invalid = f.e.dispatch(Request {
        request_id: "invalid-page".into(),
        version: 1,
        method: "library.searchPage".into(),
        params: json!({"query":"", "kind":"all", "quality":"all", "limit":201, "offset":0}),
    });
    assert_eq!(invalid.error.unwrap().code, "INVALID_PAGE");
}
