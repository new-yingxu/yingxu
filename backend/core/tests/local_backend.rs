use serde_json::json;
use std::{fs, path::PathBuf};
use yingxu_core::{
    Engine,
    fsops::{self, SafeDir},
    model::*,
};

struct Fixture {
    _tmp: tempfile::TempDir,
    root: PathBuf,
    source: PathBuf,
    targets: Vec<PathBuf>,
    engine: Engine,
}
impl Fixture {
    fn new(targets: usize) -> Self {
        let tmp = tempfile::tempdir().unwrap();
        let root = tmp.path().canonicalize().unwrap();
        let source = root.join("camera");
        fs::create_dir(&source).unwrap();
        let targets = (0..targets)
            .map(|n| {
                let p = root.join(format!("target{n}"));
                fs::create_dir(&p).unwrap();
                p
            })
            .collect();
        let engine = Engine::open(root.join("db")).unwrap();
        Self {
            _tmp: tmp,
            root,
            source,
            targets,
            engine,
        }
    }
    fn file(&self, name: &str, data: &[u8]) {
        let p = self.source.join(name);
        fs::create_dir_all(p.parent().unwrap()).unwrap();
        fs::write(p, data).unwrap();
    }
    fn request(&self) -> PlanRequest {
        PlanRequest {
            source: self.source.clone(),
            destinations: self.targets.clone(),
            name: "camera import".into(),
            verify: true,
            transfer: false,
            selected: vec![],
            subdirectory: None,
        }
    }
    fn start(&self) -> Job {
        let p = self.engine.preview(self.request()).unwrap();
        self.engine.start(&p.id, true, &id()).unwrap()
    }
    fn run(&self) -> Job {
        self.engine.worker().unwrap().run_next().unwrap().unwrap()
    }
}

#[test]
fn real_copy_sha256_raw_video_and_sidecar_preserve_source() {
    let f = Fixture::new(1);
    f.file("DCIM/a.ARW", b"raw bytes");
    f.file("DCIM/b.MP4", b"video bytes");
    f.file("DCIM/b.xmp", b"metadata");
    let job = f.start();
    assert_eq!(job.state, "queued");
    assert!(!f.targets[0].join("DCIM/a.ARW").exists());
    let done = f.run();
    assert_eq!(done.state, "success");
    assert_eq!(done.targets[0].verified, 3);
    for i in &done.items {
        assert_eq!(
            fs::read(f.source.join(&i.relative)).unwrap(),
            fs::read(&i.output).unwrap()
        );
    }
    assert_eq!(f.engine.search("", "all", "all").unwrap().len(), 3);
}
#[test]
fn conflicts_skip_same_and_number_different_without_overwrite() {
    let f = Fixture::new(1);
    f.file("same.jpg", b"same");
    f.file("different.jpg", b"new");
    fs::write(f.targets[0].join("same.jpg"), b"same").unwrap();
    fs::write(f.targets[0].join("different.jpg"), b"old").unwrap();
    f.start();
    let done = f.run();
    assert_eq!(done.targets[0].skipped, 1);
    assert_eq!(done.targets[0].copied, 1);
    assert_eq!(
        fs::read(f.targets[0].join("different.jpg")).unwrap(),
        b"old"
    );
    assert_eq!(
        fs::read(f.targets[0].join("different (1).jpg")).unwrap(),
        b"new"
    );
}
#[test]
fn missing_source_is_not_fake_success() {
    let f = Fixture::new(1);
    f.file("a.jpg", b"a");
    f.start();
    fs::rename(&f.source, f.root.join("removed-card")).unwrap();
    let j = f.run();
    assert_eq!(j.state, "failed");
    assert!(!f.targets[0].join("a.jpg").exists());
    assert!(f.root.join("removed-card/a.jpg").exists());
}
#[test]
fn changed_source_before_confirm_invalidates_plan() {
    let f = Fixture::new(1);
    f.file("a.jpg", b"a");
    let p = f.engine.preview(f.request()).unwrap();
    f.file("a.jpg", b"new");
    assert_eq!(
        f.engine.start(&p.id, true, "key").unwrap_err().code,
        "SOURCE_CHANGED"
    );
    assert_eq!(f.engine.store.jobs().unwrap().len(), 0);
}
#[test]
fn changed_source_after_queue_is_preserved_and_fails() {
    let f = Fixture::new(1);
    f.file("a.jpg", b"a");
    f.start();
    f.file("a.jpg", b"new");
    let j = f.run();
    assert_eq!(j.state, "failed");
    assert_eq!(fs::read(f.source.join("a.jpg")).unwrap(), b"new");
}
#[test]
fn reject_nested_paths_and_network_paths() {
    let f = Fixture::new(1);
    f.file("a", b"a");
    let sub = f.source.join("bad-target");
    fs::create_dir(&sub).unwrap();
    let mut r = f.request();
    r.destinations = vec![sub];
    assert_eq!(f.engine.preview(r).unwrap_err().code, "OVERLAPPING_PATHS");
    let mut r = f.request();
    r.source = PathBuf::from("//server/share");
    assert_eq!(f.engine.preview(r).unwrap_err().code, "LOCAL_PATH_REQUIRED");
}
#[cfg(unix)]
#[test]
fn symlinks_cannot_escape_source_or_target() {
    use std::os::unix::fs::symlink;
    let f = Fixture::new(1);
    f.file("a.jpg", b"a");
    symlink(f.root.join("outside"), f.source.join("escape")).unwrap();
    // 符号链接不再让整次扫描/预览失败，而是被跳过——但绝不能跟随到来源之外
    let p = f.engine.preview(f.request()).unwrap();
    let report = fsops::scan_report(&f.source).unwrap();
    assert_eq!(report.files.len(), 1, "只应收录真实文件");
    assert!(
        report
            .skipped
            .iter()
            .any(|s| s.reason == "symlink" && s.relative == "escape"),
        "符号链接必须出现在跳过清单里"
    );
    assert!(
        report
            .files
            .iter()
            .all(|file| file.relative != std::path::Path::new("escape")),
        "符号链接本身不能进入待复制清单"
    );
    assert!(!p.id.is_empty());
    fs::remove_file(f.source.join("escape")).unwrap();
    f.file("sub/b.jpg", b"b");
    let p = f.engine.preview(f.request()).unwrap();
    let j = f.engine.start(&p.id, true, "key").unwrap();
    fs::create_dir(f.root.join("outside")).unwrap();
    symlink(f.root.join("outside"), f.targets[0].join("sub")).unwrap();
    let done = f.run();
    assert_eq!(done.state, "partial");
    assert!(!f.root.join("outside/b.jpg").exists());
    assert_eq!(
        f.engine.store.job(&j.id).unwrap().targets[0].state,
        "partial"
    );
}
#[test]
fn root_replacement_after_confirm_does_not_redirect_copy() {
    let f = Fixture::new(1);
    f.file("a.jpg", b"a");
    f.start();
    fs::rename(&f.targets[0], f.root.join("old-target")).unwrap();
    fs::create_dir(&f.targets[0]).unwrap();
    let done = f.run();
    assert_eq!(done.state, "failed");
    assert!(!f.targets[0].join("a.jpg").exists());
}
#[test]
fn idempotent_start_returns_one_persisted_job() {
    let f = Fixture::new(1);
    f.file("a", b"a");
    let p = f.engine.preview(f.request()).unwrap();
    let a = f.engine.start(&p.id, true, "request-1").unwrap();
    let b = f.engine.start(&p.id, true, "request-1").unwrap();
    assert_eq!(a.id, b.id);
    assert_eq!(f.engine.store.jobs().unwrap().len(), 1);
    assert_eq!(
        f.engine.start(&p.id, false, "request-2").unwrap_err().code,
        "CONFIRMATION_REQUIRED"
    );
}
#[test]
fn pause_resume_cancel_validate_transitions() {
    let f = Fixture::new(1);
    f.file("a", b"a");
    let j = f.start();
    f.engine.control(&j.id, "pause", None).unwrap();
    let w = f.engine.worker().unwrap();
    assert!(w.run_next().unwrap().is_none());
    f.engine.control(&j.id, "resume", None).unwrap();
    assert_eq!(w.run_next().unwrap().unwrap().state, "success");
    assert_eq!(
        f.engine.control(&j.id, "resume", None).unwrap_err().code,
        "INVALID_TRANSITION"
    );
    drop(w);
    let j = f.start();
    f.engine.control(&j.id, "cancel", None).unwrap();
    assert!(f.engine.worker().unwrap().run_next().unwrap().is_none());
    assert_eq!(f.engine.store.job(&j.id).unwrap().state, "cancelled");
}
#[test]
fn interruption_mid_copy_leaves_no_final_and_retains_source() {
    let f = Fixture::new(1);
    let data = vec![42; 4 * 1024 * 1024];
    f.file("large.MP4", &data);
    let s = SafeDir::open(&f.source).unwrap();
    let t = SafeDir::open(&f.targets[0]).unwrap();
    let fp = fsops::fingerprint(&s.read("large.MP4".as_ref()).unwrap().metadata().unwrap());
    let mut checks = 0;
    let e = fsops::copy_checked(
        &s,
        "large.MP4".as_ref(),
        &fp,
        &t,
        "large.MP4".as_ref(),
        true,
        None,
        &mut || {
            checks += 1;
            if checks > 3 {
                Err(CoreError::new("INTERRUPTED", "cancelled"))
            } else {
                Ok(())
            }
        },
    )
    .unwrap_err();
    assert_eq!(e.code, "INTERRUPTED");
    assert_eq!(fs::read(f.source.join("large.MP4")).unwrap(), data);
    assert_eq!(fs::read_dir(&f.targets[0]).unwrap().count(), 0);
}
#[test]
fn single_worker_and_persisted_recovery_require_resume() {
    let f = Fixture::new(1);
    f.file("a", b"a");
    let j = f.start();
    f.engine
        .store
        .connect()
        .unwrap()
        .execute("UPDATE jobs SET state='running' WHERE id=?1", [&j.id])
        .unwrap();
    let reopened = Engine::open(f.root.join("db")).unwrap();
    let w = reopened.worker().unwrap();
    assert_eq!(reopened.store.job(&j.id).unwrap().state, "paused");
    assert_eq!(
        reopened.worker().err().unwrap().code,
        "WORKER_ALREADY_RUNNING"
    );
    reopened.control(&j.id, "resume", None).unwrap();
    assert_eq!(w.run_next().unwrap().unwrap().state, "success");
}
#[test]
fn only_retry_failed_destination_preserves_successful_replica() {
    let f = Fixture::new(2);
    f.file("a.jpg", b"a");
    let j = f.start();
    fs::rename(&f.targets[1], f.root.join("unplugged")).unwrap();
    let done = f.run();
    assert_eq!(done.state, "partial");
    let first = fsops::fingerprint(&fs::metadata(f.targets[0].join("a.jpg")).unwrap());
    fs::rename(f.root.join("unplugged"), &f.targets[1]).unwrap();
    let bad = done.targets.iter().find(|t| t.state == "failed").unwrap();
    f.engine.control(&j.id, "retry", Some(&bad.id)).unwrap();
    let done = f.run();
    assert_eq!(done.state, "success");
    assert_eq!(
        fsops::fingerprint(&fs::metadata(f.targets[0].join("a.jpg")).unwrap()),
        first
    );
}
#[test]
fn unchecked_backup_is_reported_unverified_and_transfer_cannot_skip_sha() {
    let f = Fixture::new(1);
    f.file("a", b"a");
    let mut r = f.request();
    r.verify = false;
    let p = f.engine.preview(r.clone()).unwrap();
    f.engine.start(&p.id, true, "a").unwrap();
    let done = f.run();
    assert_eq!(done.state, "success");
    assert_eq!(done.targets[0].verified, 0);
    assert_eq!(done.phase, "copied_without_verification");
    r.transfer = true;
    assert_eq!(
        f.engine.preview(r).unwrap_err().code,
        "TRANSFER_REQUIRES_VERIFY"
    );
}
#[test]
fn checksum_mismatch_does_not_commit_corrupt_restore() {
    let f = Fixture::new(1);
    f.file("a", b"a");
    let s = SafeDir::open(&f.source).unwrap();
    let t = SafeDir::open(&f.targets[0]).unwrap();
    let fp = fsops::fingerprint(&s.read("a".as_ref()).unwrap().metadata().unwrap());
    let e = fsops::copy_checked(
        &s,
        "a".as_ref(),
        &fp,
        &t,
        "a".as_ref(),
        true,
        Some("wrong"),
        &mut || Ok(()),
    )
    .unwrap_err();
    assert_eq!(e.code, "EXPECTED_CHECKSUM_MISMATCH");
    assert_eq!(fs::read_dir(&f.targets[0]).unwrap().count(), 0);
}
#[test]
fn audit_detects_tampering_restore_uses_only_healthy_data() {
    let f = Fixture::new(2);
    f.file("a.jpg", b"a");
    f.start();
    f.run();
    fs::write(f.targets[0].join("a.jpg"), b"corrupt").unwrap();
    let a = f.engine.audit_replicas().unwrap();
    assert_eq!(a["healthy"], 1);
    assert_eq!(a["issues"][0]["recoverable"], true);
    let replicas = f.engine.replicas().unwrap();
    let bad = replicas.iter().find(|r| r["verified"] == false).unwrap()["id"]
        .as_str()
        .unwrap();
    let restore = f.root.join("restored");
    fs::create_dir(&restore).unwrap();
    assert_eq!(
        f.engine
            .restore(bad, &restore, true, "bad")
            .unwrap_err()
            .code,
        "REPLICA_DAMAGED"
    );
    let good = replicas.iter().find(|r| r["verified"] == true).unwrap()["id"]
        .as_str()
        .unwrap();
    f.engine.restore(good, &restore, true, "good").unwrap();
    assert_eq!(f.run().state, "success");
    assert_eq!(fs::read(restore.join("a.jpg")).unwrap(), b"a");
}
#[test]
fn transfer_needs_fresh_second_confirmation_and_can_be_undone() {
    let f = Fixture::new(1);
    f.file("a.jpg", b"a");
    let mut r = f.request();
    r.transfer = true;
    let p = f.engine.preview(r).unwrap();
    let j = f.engine.start(&p.id, true, "transfer").unwrap();
    f.run();
    assert!(f.source.join("a.jpg").exists());
    let removal = f.engine.prepare_source_removal(&j.id).unwrap();
    assert_eq!(
        f.engine
            .confirm_source_removal(&removal.token, "yes")
            .unwrap_err()
            .code,
        "CONFIRMATION_REQUIRED"
    );
    f.engine
        .confirm_source_removal(&removal.token, "将来源移入可恢复目录")
        .unwrap();
    assert!(!f.source.join("a.jpg").exists());
    assert_eq!(
        fs::read(
            f.source
                .join(".yingxu-transfer-recovery")
                .join(&j.id)
                .join("a.jpg")
        )
        .unwrap(),
        b"a"
    );
    assert_eq!(
        f.engine
            .confirm_source_removal(&removal.token, "将来源移入可恢复目录")
            .unwrap_err()
            .code,
        "NOT_FOUND"
    );
    f.engine.undo_source_removal(&j.id, true).unwrap();
    assert_eq!(fs::read(f.source.join("a.jpg")).unwrap(), b"a");
}
#[test]
fn transfer_preserves_changed_source_or_damaged_target() {
    for damage_source in [true, false] {
        let f = Fixture::new(1);
        f.file("a.jpg", b"a");
        let mut r = f.request();
        r.transfer = true;
        let p = f.engine.preview(r).unwrap();
        let j = f.engine.start(&p.id, true, "t").unwrap();
        f.run();
        let removal = f.engine.prepare_source_removal(&j.id).unwrap();
        if damage_source {
            f.file("a.jpg", b"changed");
        } else {
            fs::write(f.targets[0].join("a.jpg"), b"broken").unwrap();
        }
        assert!(
            f.engine
                .confirm_source_removal(&removal.token, "将来源移入可恢复目录")
                .is_err()
        );
        assert!(f.source.join("a.jpg").exists());
    }
}
#[test]
fn search_settings_reports_and_history_are_persistent() {
    let f = Fixture::new(1);
    f.file("海边日落.jpg", b"photo");
    f.start();
    let done = f.run();
    assert_eq!(
        f.engine
            .search("日落", "photo", "unanalyzed")
            .unwrap()
            .len(),
        1
    );
    assert!(
        f.engine
            .search("' OR 1=1 --", "all", "all")
            .unwrap()
            .is_empty()
    );
    f.engine
        .update_settings(json!({"theme":"dark","similarity":90}))
        .unwrap();
    assert_eq!(
        f.engine
            .update_settings(json!({"token":"secret"}))
            .unwrap_err()
            .code,
        "UNKNOWN_SETTING"
    );
    let output = f.root.join("reports");
    fs::create_dir(&output).unwrap();
    for format in ["json", "md", "txt", "csv"] {
        let p = f
            .engine
            .export_report(&done.id, &output, format, true)
            .unwrap();
        assert!(p.is_file());
        assert!(
            f.engine
                .export_report(&done.id, &output, format, true)
                .is_err()
        );
    }
    let reopened = Engine::open(f.root.join("db")).unwrap();
    assert_eq!(
        reopened
            .store
            .get::<serde_json::Value>("settings", "preferences")
            .unwrap()["theme"],
        "dark"
    );
    reopened.clear_history("清空归档记录").unwrap();
    assert!(f.targets[0].join("海边日落.jpg").exists());
    assert!(!reopened.replicas().unwrap().is_empty());
    assert!(reopened.store.jobs().unwrap().is_empty());
}

#[test]
fn paged_library_search_returns_all_items_without_duplicates() {
    let f = Fixture::new(1);
    for index in 0..501 {
        f.file(&format!("DCIM/{index:04}.jpg"), b"photo");
    }
    let index = f.engine.index_source(&f.source).unwrap();
    assert_eq!(index["files"], 501);

    let first = f.engine.search_page("", "photo", "all", 200, 0).unwrap();
    let second = f.engine.search_page("", "photo", "all", 200, 200).unwrap();
    let third = f.engine.search_page("", "photo", "all", 200, 400).unwrap();
    assert_eq!(first["total"], 501);
    assert_eq!(first["items"].as_array().unwrap().len(), 200);
    assert_eq!(first["hasMore"], true);
    assert_eq!(second["items"].as_array().unwrap().len(), 200);
    assert_eq!(second["hasMore"], true);
    assert_eq!(third["items"].as_array().unwrap().len(), 101);
    assert_eq!(third["hasMore"], false);

    let ids: std::collections::HashSet<_> = first["items"]
        .as_array()
        .unwrap()
        .iter()
        .chain(second["items"].as_array().unwrap())
        .chain(third["items"].as_array().unwrap())
        .map(|item| item["id"].as_str().unwrap())
        .collect();
    assert_eq!(ids.len(), 501);

    let reply = f.engine.dispatch(yingxu_core::api::Request {
        request_id: "paged-search".into(),
        version: 1,
        method: "library.searchPage".into(),
        params: json!({"query":"","kind":"photo","quality":"all","limit":50,"offset":450}),
    });
    assert!(reply.ok);
    assert_eq!(reply.data.unwrap()["items"].as_array().unwrap().len(), 50);
    assert_eq!(
        f.engine
            .search_page("", "photo", "all", 201, 0)
            .unwrap_err()
            .code,
        "INVALID_PAGE"
    );
}
#[test]
fn outbox_defaults_do_not_include_paths() {
    let f = Fixture::new(1);
    f.file("a.jpg", b"photo");
    f.start();
    f.run();
    let c = f.engine.store.connect().unwrap();
    let payload: String = c
        .query_row("SELECT payload FROM outbox_events", [], |r| r.get(0))
        .unwrap();
    assert!(!payload.contains(f.source.to_str().unwrap()));
    assert!(!payload.contains(f.targets[0].to_str().unwrap()));
    let v: serde_json::Value = serde_json::from_str(&payload).unwrap();
    assert!(v["source"].is_null());
}
#[test]
fn newer_database_version_is_refused() {
    let f = Fixture::new(1);
    f.engine
        .store
        .connect()
        .unwrap()
        .pragma_update(None, "user_version", 99)
        .unwrap();
    assert_eq!(
        Engine::open(f.root.join("db")).err().unwrap().code,
        "DATABASE_VERSION_NEWER"
    );
}
