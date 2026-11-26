-- A store as this code wrote it at layout version 1. Never edit this file:
-- a change that breaks old data must fail the test that reads it.
PRAGMA application_id = 1279348048;
PRAGMA user_version = 1;
BEGIN TRANSACTION;
CREATE TABLE effects (
  effect_id    INTEGER PRIMARY KEY AUTOINCREMENT,
  effect_key   TEXT    NOT NULL UNIQUE,
  v            INTEGER NOT NULL CHECK (v >= 1),
  session_id   TEXT    NOT NULL,
  state        TEXT    NOT NULL,
  action       TEXT    NOT NULL,
  probe        TEXT,
  undo         TEXT,
  result       TEXT,
  resolution   TEXT,
  begun_at     TEXT    NOT NULL,
  finished_at  TEXT,
  resolved_at  TEXT
) STRICT;
INSERT INTO "effects" VALUES(1,'665c55b952f1563d7fe40b394b3640eef0cd920cad33deead0dbef1524a5540a',1,'sess-a','done','{"action_class":"reversible_write","action_hash":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","args":{"path":"notes.txt","ratio":0.5,"text":"tea"},"attempt":0,"call_index":0,"effect_key":"665c55b952f1563d7fe40b394b3640eef0cd920cad33deead0dbef1524a5540a","outward":false,"reply_seq":3,"session_id":"sess-a","target":"file:notes.txt","tool":"write_file","v":1}',NULL,'{"blob":"dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd","kind":"restore_file","v":1}','{"external_id":"file-7","outcome":"done","output":{"bytes":3},"undo":{"blob":"dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd","kind":"restore_file","v":1}}',NULL,'2026-11-26T07:35:12.345Z','2026-11-26T07:35:12.345Z',NULL);
INSERT INTO "effects" VALUES(2,'d48a4571ca782fa9ade644c150e2da45e6199a08434691249b920b0640ba95e3',1,'sess-b','intended','{"action_class":"reversible_write","action_hash":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","args":{"path":"notes.txt","ratio":0.5,"text":"tea"},"attempt":0,"call_index":0,"effect_key":"d48a4571ca782fa9ade644c150e2da45e6199a08434691249b920b0640ba95e3","outward":false,"reply_seq":1,"session_id":"sess-b","target":"file:notes.txt","tool":"write_file","v":1}',NULL,NULL,NULL,NULL,'2026-11-26T07:35:12.345Z',NULL,NULL);
CREATE TABLE events (
  pos          INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id   TEXT    NOT NULL,
  seq          INTEGER NOT NULL CHECK (seq >= 1),
  type         TEXT    NOT NULL,
  v            INTEGER NOT NULL CHECK (v >= 1),
  event_id     TEXT    NOT NULL,
  run_id       TEXT,
  step         INTEGER,
  at           TEXT    NOT NULL,
  duration_ms  INTEGER,
  cause        TEXT,
  payload      TEXT    NOT NULL,
  UNIQUE (session_id, seq),
  UNIQUE (event_id)
) STRICT;
INSERT INTO "events" VALUES(1,'sess-a',1,'user_message',1,'4fe4741d8507af924e88a6278fb55d33a63e0003ac1c2a4fa91bc0e9d1e2070f',NULL,NULL,'2026-11-26T07:35:12.345Z',NULL,NULL,'{"text":"What is on my list?"}');
INSERT INTO "events" VALUES(2,'sess-a',2,'run_started',1,'d7559fd3c1cf24a600e0824469a03b3e42f595ba4d439105667ea9adee557bc4','run-1',NULL,'2026-11-26T07:35:12.345Z',NULL,NULL,'{}');
INSERT INTO "events" VALUES(3,'sess-a',3,'model_response',1,'d518d57daecc586ac84e6e9f446d4767ee0bc980cc466ee60c019a40589047ce','run-1',1,'2026-11-26T07:35:12.345Z',2150,NULL,'{"end":{"kind":"stop","model_reported":"made-up-1","provider_reason":"tool_use","reason":"tool_calls","skipped":0},"message":{"parts":[{"kind":"reasoning","made_by":"scripted","opaque":{"ratio":0.5,"sealed":"c2VhbGVk","tags":["a",null,true]},"text":""},{"kind":"text","text":"I will look."},{"arguments":{"path":"list.txt"},"id":"call_a","kind":"tool_call","name":"read_file","parse_error":null,"raw_arguments":null}],"role":"assistant"},"usage":{"cache_read":0,"cache_write":0,"input_uncached":120,"output":40}}');
INSERT INTO "events" VALUES(4,'sess-a',4,'tool_result',1,'adb1db95c3d9d7a36d516ac75bb3d0cd5ef0a22a2fd28026b9bddbd615f76570','run-1',1,'2026-11-26T07:35:12.345Z',NULL,NULL,'{"call_id":"call_a","is_error":false,"text":"milk\neggs\ntea"}');
INSERT INTO "events" VALUES(5,'sess-a',5,'run_end',1,'0f42d1ceb0e816eecb18f9b68b0fafc3980babc38d42f591652e27c2143658df','run-1',NULL,'2026-11-26T07:35:12.345Z',NULL,NULL,'{"reason":"done"}');
INSERT INTO "events" VALUES(6,'sess-b',1,'run_started',1,'e15e130b71d3c22ce674c29d3b6442ea033f2ee95efe60b8e550b87d472218fb','run-2',NULL,'2026-11-26T07:35:12.345Z',NULL,NULL,'{}');
CREATE TABLE inbox (
  inbox_id           INTEGER PRIMARY KEY AUTOINCREMENT,
  channel            TEXT    NOT NULL,
  native_id          TEXT    NOT NULL,
  inbound            TEXT    NOT NULL,
  routed_session_id  TEXT,
  routed_seq         INTEGER,
  UNIQUE (channel, native_id)
) STRICT;
INSERT INTO "inbox" VALUES(1,'telegram','8812','{"answer":null,"approval_id":null,"attachments":[{"media_type":"image/jpeg","sha256":"9999999999999999999999999999999999999999999999999999999999999999","size":1204}],"channel":"telegram","command":null,"conversation":"chat-1","kind":"message","native_id":"8812","received_at":"2026-11-26T07:35:12.345Z","sender":{"id":"user-1","verified":true},"sent_at":"2026-11-26T07:35:10.000Z","text":"What is on my shopping list?","v":1}','sess-a',1);
INSERT INTO "inbox" VALUES(2,'telegram','8813','{"answer":null,"approval_id":null,"attachments":[{"media_type":"image/jpeg","sha256":"9999999999999999999999999999999999999999999999999999999999999999","size":1204}],"channel":"telegram","command":null,"conversation":"chat-1","kind":"message","native_id":"8813","received_at":"2026-11-26T07:35:12.345Z","sender":{"id":"user-1","verified":true},"sent_at":"2026-11-26T07:35:10.000Z","text":"What is on my shopping list?","v":1}',NULL,NULL);
CREATE INDEX events_run_marks ON events (session_id, seq, type)
  WHERE type IN ('run_started', 'run_end');
CREATE INDEX effects_unresolved ON effects (effect_id)
  WHERE state IN ('intended', 'unknown');
CREATE INDEX inbox_pending ON inbox (inbox_id)
  WHERE routed_session_id IS NULL;
DELETE FROM "sqlite_sequence";
INSERT INTO "sqlite_sequence" VALUES('inbox',2);
INSERT INTO "sqlite_sequence" VALUES('events',6);
INSERT INTO "sqlite_sequence" VALUES('effects',2);
COMMIT;
