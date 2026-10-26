CREATE TABLE schema_migrations (
  version INT NOT NULL PRIMARY KEY,
  applied_at DATETIME(6) NOT NULL
) ENGINE=InnoDB;

INSERT INTO schema_migrations (version, applied_at) VALUES (1, UTC_TIMESTAMP(6));

CREATE TABLE devices (
  id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
  owner_id VARCHAR(160) NOT NULL,
  device_id VARCHAR(500) NOT NULL,
  origin VARCHAR(16) NOT NULL,
  display_name VARCHAR(120) NOT NULL DEFAULT '',
  revoked BOOLEAN NOT NULL DEFAULT FALSE,
  created_at DATETIME(6) NOT NULL,
  last_seen_at DATETIME(6) NOT NULL,
  UNIQUE KEY uq_device_owner_id (owner_id, device_id),
  KEY ix_devices_owner_id (owner_id)
) ENGINE=InnoDB;

CREATE TABLE sync_events (
  id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
  owner_id VARCHAR(160) NOT NULL,
  device_id VARCHAR(500) NOT NULL,
  event_id VARCHAR(500) NOT NULL,
  sequence BIGINT NOT NULL,
  body_json JSON NOT NULL,
  created_at DATETIME(6) NOT NULL,
  UNIQUE KEY uq_sync_owner_event (owner_id, event_id),
  UNIQUE KEY uq_sync_device_sequence (owner_id, device_id, sequence),
  KEY ix_sync_events_owner_id (owner_id),
  KEY ix_sync_events_device_id (device_id)
) ENGINE=InnoDB;

CREATE TABLE agent_actions (
  id VARCHAR(500) NOT NULL,
  owner_id VARCHAR(160) NOT NULL,
  device_id VARCHAR(500) NOT NULL,
  origin VARCHAR(16) NOT NULL,
  tool VARCHAR(64) NOT NULL,
  state VARCHAR(40) NOT NULL,
  proposal_json JSON NOT NULL,
  created_at DATETIME(6) NOT NULL,
  expires_at DATETIME(6) NOT NULL,
  PRIMARY KEY (owner_id, id),
  KEY ix_agent_actions_owner_id (owner_id)
) ENGINE=InnoDB;

CREATE TABLE sync_preferences (
  owner_id VARCHAR(160) NOT NULL PRIMARY KEY,
  sync_records BOOLEAN NOT NULL DEFAULT TRUE,
  sync_agent_sessions BOOLEAN NOT NULL DEFAULT TRUE,
  sync_thumbnails BOOLEAN NOT NULL DEFAULT FALSE,
  sync_paths BOOLEAN NOT NULL DEFAULT FALSE,
  updated_at DATETIME(6) NOT NULL
) ENGINE=InnoDB;
