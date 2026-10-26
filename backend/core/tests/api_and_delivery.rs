use serde_json::{Value, json};
use std::{
    fs,
    io::{Read, Write},
    process::{Command, Stdio},
    time::{Duration, Instant},
};
use tempfile::TempDir;
use yingxu_core::{Engine, PlanRequest, api::Request};
struct F {
    _tmp: TempDir,
    e: Engine,
    source: std::path::PathBuf,
    target: std::path::PathBuf,
    output: std::path::PathBuf,
}
impl F {
    fn new() -> Self {
        let t = TempDir::new().unwrap();
        let root = t.path().canonicalize().unwrap();
        let source = root.join("source");
        let target = root.join("target");
        let output = root.join("output");
        for p in [&source, &target, &output] {
            fs::create_dir(p).unwrap();
        }
        let e = Engine::open(t.path().join("db")).unwrap();
        Self {
            _tmp: t,
            e,
            source,
            target,
            output,
        }
    }
    fn queue(&self) -> yingxu_core::Job {
        let p = self
            .e
            .preview(PlanRequest {
                source: self.source.clone(),
                destinations: vec![self.target.clone()],
                name: "backup".into(),
                verify: true,
                transfer: false,
                selected: vec![],
                subdirectory: None,
            })
            .unwrap();
        self.e.start(&p.id, true, "backup-key").unwrap()
    }
    fn run(&self) -> yingxu_core::Job {
        self.e.worker().unwrap().run_next().unwrap().unwrap()
    }
}
#[test]
fn delivery_writes_verified_manifest_and_repeat_is_idempotent() {
    let f = F::new();
    fs::write(f.source.join("照片 & test.jpg"), b"test photo bytes").unwrap();
    f.queue();
    f.run();
    let media = f.e.search("", "all", "all").unwrap()[0]["id"]
        .as_str()
        .unwrap()
        .to_string();
    let j =
        f.e.create_delivery(
            "客户交付",
            std::slice::from_ref(&media),
            &f.output,
            true,
            true,
            true,
            "delivery-key",
        )
        .unwrap();
    assert_eq!(
        f.e.create_delivery(
            "客户交付",
            std::slice::from_ref(&media),
            &f.output,
            true,
            true,
            true,
            "delivery-key"
        )
        .unwrap()
        .id,
        j.id
    );
    assert_eq!(
        f.e.create_delivery(
            "其他",
            &[media],
            &f.output,
            true,
            true,
            true,
            "delivery-key"
        )
        .unwrap_err()
        .code,
        "IDEMPOTENCY_CONFLICT"
    );
    let done = f.run();
    assert_eq!(done.state, "success");
    let base = f
        .output
        .join(j.options["packageDirectory"].as_str().unwrap());
    let manifest: Value =
        serde_json::from_slice(&fs::read(base.join("manifest.json")).unwrap()).unwrap();
    assert_eq!(
        manifest["entries"][0]["sha256"],
        done.items[0].sha256.as_deref().unwrap()
    );
    assert!(
        fs::read_to_string(base.join("contact-sheet.html"))
            .unwrap()
            .contains("%26")
    );
    assert!(base.join("SHA256SUMS.txt").is_file());
    assert_eq!(
        fs::read(f.source.join("照片 & test.jpg")).unwrap(),
        b"test photo bytes"
    );
}
#[test]
fn delivery_damaged_replica_never_commits() {
    let f = F::new();
    fs::write(f.source.join("a.jpg"), b"photo").unwrap();
    f.queue();
    f.run();
    let media = f.e.search("", "all", "all").unwrap()[0]["id"]
        .as_str()
        .unwrap()
        .to_string();
    f.e.create_delivery("客户", &[media], &f.output, true, true, true, "d")
        .unwrap();
    fs::write(f.target.join("a.jpg"), b"broken").unwrap();
    assert_eq!(f.run().state, "failed");
    let r = f.e.dispatch(Request {
        request_id: "d".into(),
        version: 1,
        method: "delivery.list".into(),
        params: json!({}),
    });
    assert_eq!(r.data.unwrap(), json!([]));
}
#[test]
fn restore_replay_and_report_rename() {
    let f = F::new();
    fs::write(f.source.join("a"), b"a").unwrap();
    let j = f.queue();
    f.run();
    f.e.rename_history(&j.id, "新记录", true).unwrap();
    assert_eq!(f.e.report(&j.id).unwrap()["job"]["name"], "新记录");
    let id = f.e.replicas().unwrap()[0]["id"]
        .as_str()
        .unwrap()
        .to_string();
    let a = f.e.restore(&id, &f.output, true, "restore").unwrap();
    assert_eq!(
        f.e.restore(&id, &f.output, true, "restore").unwrap().id,
        a.id
    );
    assert_eq!(f.run().state, "success");
}
#[test]
fn rpc_rejects_bad_versions_unknown_fields_and_fake_cloud() {
    let f = F::new();
    for (method, version, params, code) in [
        ("health", 2, json!({}), "UNSUPPORTED_PROTOCOL"),
        ("jobs.start", 1, json!({"surprise":true}), "INVALID_PARAMS"),
        ("auth.login", 1, json!({}), "CLOUD_ADAPTER_NOT_CONNECTED"),
        ("shell.exec", 1, json!({}), "UNKNOWN_METHOD"),
    ] {
        let r = f.e.dispatch(Request {
            request_id: "test".into(),
            version,
            method: method.into(),
            params,
        });
        assert_eq!(r.error.unwrap().code, code);
    }
}
#[test]
fn cli_recovers_after_invalid_json() {
    let f = F::new();
    let mut child = Command::new(env!("CARGO_BIN_EXE_yingxu-core"))
        .args(["--data-dir", f.e.store.root.to_str().unwrap(), "rpc"])
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .spawn()
        .unwrap();
    let mut input = child.stdin.take().unwrap();
    writeln!(input, "bad JSON").unwrap();
    writeln!(
        input,
        "{}",
        json!({"requestId":"health","version":1,"method":"health","params":{}})
    )
    .unwrap();
    drop(input);
    let out = child.wait_with_output().unwrap();
    assert!(out.status.success());
    let lines = String::from_utf8(out.stdout).unwrap();
    let rows: Vec<Value> = lines
        .lines()
        .map(|s| serde_json::from_str(s).unwrap())
        .collect();
    assert_eq!(rows[0]["ok"], false);
    assert_eq!(rows[1]["ok"], true);
}
#[test]
fn separate_process_kill_recovers_owned_temp_and_requires_resume() {
    let f = F::new();
    let file = fs::File::create(f.source.join("large.MP4")).unwrap();
    file.set_len(256 * 1024 * 1024).unwrap();
    let j = f.queue();
    let mut child = Command::new(env!("CARGO_BIN_EXE_yingxu-core"))
        .args([
            "--data-dir",
            f.e.store.root.to_str().unwrap(),
            "worker-drain",
        ])
        .stdout(Stdio::null())
        .spawn()
        .unwrap();
    let deadline = Instant::now() + Duration::from_secs(15);
    let mut observed = false;
    while Instant::now() < deadline {
        let c = f.e.store.connect().unwrap();
        let n: i64 = c
            .query_row("SELECT count(*) FROM pending_writes", [], |r| r.get(0))
            .unwrap();
        if n > 0 {
            observed = true;
            break;
        }
        if child.try_wait().unwrap().is_some() {
            break;
        }
        std::thread::sleep(Duration::from_millis(2));
    }
    assert!(observed, "must observe active copy before kill");
    child.kill().unwrap();
    child.wait().unwrap();
    let worker = f.e.worker().unwrap();
    assert_eq!(f.e.store.job(&j.id).unwrap().state, "paused");
    assert!(fs::read_dir(&f.target).unwrap().all(|r| {
        !r.unwrap()
            .file_name()
            .to_string_lossy()
            .starts_with(".yingxu-part-")
    }));
    assert_eq!(
        fs::metadata(f.source.join("large.MP4")).unwrap().len(),
        256 * 1024 * 1024
    );
    f.e.control(&j.id, "resume", None).unwrap();
    let done = worker.run_next().unwrap().unwrap();
    assert_eq!(done.state, "success");
    assert_eq!(done.targets[0].verified, 1);
    let mut bytes = [1; 8];
    fs::File::open(&done.items[0].output)
        .unwrap()
        .read_exact(&mut bytes)
        .unwrap();
    assert_eq!(bytes, [0; 8]);
}
