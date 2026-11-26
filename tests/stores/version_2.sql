-- A store as this code wrote it at layout version 2, with every event type
-- the loop writes and an outbox row in each state. Never edit this file:
-- a change that breaks old data must fail the test that reads it.
PRAGMA application_id = 1279348048;
PRAGMA user_version = 2;
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
INSERT INTO "events" VALUES(1,'sess-a',1,'session_started',1,'a271037e92b335099dbfb7537f9d7ca9818eed454b9c46929b0d838188541489',NULL,NULL,'2026-11-26T07:35:12.345Z',NULL,NULL,'{"channel":"terminal","conversation":"sess-a"}');
INSERT INTO "events" VALUES(2,'sess-a',2,'run_started',1,'d7559fd3c1cf24a600e0824469a03b3e42f595ba4d439105667ea9adee557bc4','id-0001',NULL,'2026-11-26T07:35:12.345Z',NULL,'dd604340f38cee91a3b3307bcd58864d83408f1f3d9c014f8f20165cac25e5ce','{"config_id":"cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc","trigger":"message"}');
INSERT INTO "events" VALUES(3,'sess-a',3,'user_message',1,'dd604340f38cee91a3b3307bcd58864d83408f1f3d9c014f8f20165cac25e5ce','id-0001',NULL,'2026-11-26T07:35:12.345Z',NULL,NULL,'{"text":"Note one, and look up the weather."}');
INSERT INTO "events" VALUES(4,'sess-a',4,'model_request',1,'adb1db95c3d9d7a36d516ac75bb3d0cd5ef0a22a2fd28026b9bddbd615f76570','id-0001',1,'2026-11-26T07:35:12.345Z',NULL,NULL,'{"deadline_ms":600000}');
INSERT INTO "events" VALUES(5,'sess-a',5,'model_response',1,'0f42d1ceb0e816eecb18f9b68b0fafc3980babc38d42f591652e27c2143658df','id-0001',1,'2026-11-26T07:35:12.345Z',0,'adb1db95c3d9d7a36d516ac75bb3d0cd5ef0a22a2fd28026b9bddbd615f76570','{"cost_micro_dollars":800,"end":{"kind":"stop","model_reported":"made-up-1","provider_reason":"tool_calls","reason":"tool_calls","skipped":0},"message":{"parts":[{"arguments":{"n":1},"id":"c1","kind":"tool_call","name":"note","parse_error":null,"raw_arguments":null},{"arguments":{},"id":"c2","kind":"tool_call","name":"weather","parse_error":null,"raw_arguments":null}],"role":"assistant"},"usage":{"cache_read":0,"cache_write":0,"input_uncached":100,"output":20}}');
INSERT INTO "events" VALUES(6,'sess-a',6,'tool_intent',1,'a7e278a51c7560591fdc01ef71d47a76b1140ee849612eb492a8de572a8c1340','id-0001',1,'2026-11-26T07:35:12.345Z',NULL,'0f42d1ceb0e816eecb18f9b68b0fafc3980babc38d42f591652e27c2143658df','{"call_id":"c1","name":"note"}');
INSERT INTO "events" VALUES(7,'sess-a',7,'tool_result',1,'fa21004b2629df9f1f2408c0455ddba741a4c391a96bcf8226651ab0b15bda4c','id-0001',1,'2026-11-26T07:35:12.345Z',0,'a7e278a51c7560591fdc01ef71d47a76b1140ee849612eb492a8de572a8c1340','{"call_id":"c1","is_error":false,"text":"Noted 1."}');
INSERT INTO "events" VALUES(8,'sess-a',8,'tool_result',1,'03fca718a4292a30d4f5d3e85d80911f0d07342a6c4e5026f1c10752f1944077','id-0001',1,'2026-11-26T07:35:12.345Z',NULL,'0f42d1ceb0e816eecb18f9b68b0fafc3980babc38d42f591652e27c2143658df','{"call_id":"c2","harness":"unknown_tool","is_error":true,"text":"There is no tool named weather. The tools are: note, tick, wait."}');
INSERT INTO "events" VALUES(9,'sess-a',9,'model_request',1,'17918da67149444f954df16bb2a48a776bb61176ccfa3b4a5ed3cbd7f7226172','id-0001',2,'2026-11-26T07:35:12.345Z',NULL,NULL,'{"deadline_ms":600000}');
INSERT INTO "events" VALUES(10,'sess-a',10,'model_response',1,'c4f025ce007d62a8c9d05a8f3200d9f2e19849bb2ffe61f608341d0ed2844020','id-0001',2,'2026-11-26T07:35:12.345Z',0,'17918da67149444f954df16bb2a48a776bb61176ccfa3b4a5ed3cbd7f7226172','{"cost_micro_dollars":800,"end":{"kind":"stop","model_reported":"made-up-1","provider_reason":"end","reason":"end","skipped":0},"message":{"parts":[{"kind":"text","text":"Noted one. There is no weather tool."}],"role":"assistant"},"usage":{"cache_read":0,"cache_write":0,"input_uncached":100,"output":20}}');
INSERT INTO "events" VALUES(11,'sess-a',11,'reply_ready',1,'ebd598d18419450f69b7d9431230febb31a5d153c1ca937628b1516de5fea29d','id-0001',2,'2026-11-26T07:35:12.345Z',NULL,NULL,'{"kind":"reply","run_detail":"","run_reason":"done","text":"Noted one. There is no weather tool."}');
INSERT INTO "events" VALUES(12,'sess-a',12,'run_end',1,'295dc5e141193c952701d376d784ea8ecb3c9a68c5cd55ed370ed8760690ce60','id-0001',2,'2026-11-26T07:35:12.345Z',NULL,NULL,'{"detail":"","reason":"done"}');
INSERT INTO "events" VALUES(13,'sess-a',13,'run_started',1,'feb8e40fcd4c5eeb30440f53168747e92420d80a46d247f34c9f6acbf976ea3d','id-0002',NULL,'2026-11-26T07:35:12.345Z',NULL,'a7eab331b997c86ffbb1c43070482f348cc603b4de983b10990556e4ec7c144f','{"config_id":"cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc","trigger":"message"}');
INSERT INTO "events" VALUES(14,'sess-a',14,'user_message',1,'a7eab331b997c86ffbb1c43070482f348cc603b4de983b10990556e4ec7c144f','id-0002',NULL,'2026-11-26T07:35:12.345Z',NULL,NULL,'{"text":"Note two, then three."}');
INSERT INTO "events" VALUES(15,'sess-a',15,'model_request',1,'60d03ce1ad723b87c501312c5856b2cc15e9a6a7ec4c10b98b482e0b8d9f938f','id-0002',1,'2026-11-26T07:35:12.345Z',NULL,NULL,'{"deadline_ms":600000}');
INSERT INTO "events" VALUES(16,'sess-a',16,'model_response',1,'444ce3ce5fae81bea7654d2cfb2142209f6892b14b5e51d59f66e1c79f3b9204','id-0002',1,'2026-11-26T07:35:12.345Z',0,'60d03ce1ad723b87c501312c5856b2cc15e9a6a7ec4c10b98b482e0b8d9f938f','{"cost_micro_dollars":800,"end":{"kind":"stop","model_reported":"made-up-1","provider_reason":"tool_calls","reason":"tool_calls","skipped":0},"message":{"parts":[{"arguments":{"n":2},"id":"c3","kind":"tool_call","name":"note","parse_error":null,"raw_arguments":null},{"arguments":{"n":3},"id":"c4","kind":"tool_call","name":"note","parse_error":null,"raw_arguments":null}],"role":"assistant"},"usage":{"cache_read":0,"cache_write":0,"input_uncached":100,"output":20}}');
INSERT INTO "events" VALUES(17,'sess-a',17,'tool_intent',1,'b0967c04528de28d5b46393d412f76ed97287b57f99e6c2caf0e25fe9b7991eb','id-0002',1,'2026-11-26T07:35:12.345Z',NULL,'444ce3ce5fae81bea7654d2cfb2142209f6892b14b5e51d59f66e1c79f3b9204','{"call_id":"c3","name":"note"}');
INSERT INTO "events" VALUES(18,'sess-a',18,'tool_result',1,'c74bf28278ea01ff10420fd8471f35c317fd24e741765929cbdc1d7c5d69f324','id-0002',1,'2026-11-26T07:35:12.345Z',0,'b0967c04528de28d5b46393d412f76ed97287b57f99e6c2caf0e25fe9b7991eb','{"call_id":"c3","is_error":false,"text":"Noted 2."}');
INSERT INTO "events" VALUES(19,'sess-a',19,'cancelled',1,'623aadc5bd2670e3749c79c0bf22c1e5ea2fab4e8aed57e4309b7612664278af','id-0002',1,'2026-11-26T07:35:12.345Z',NULL,NULL,'{}');
INSERT INTO "events" VALUES(20,'sess-a',20,'tool_result',1,'44761f3c88ba6adfafdd4721ae42178ba879bb9b44770773952442740d979bbf','id-0002',1,'2026-11-26T07:35:12.345Z',NULL,'444ce3ce5fae81bea7654d2cfb2142209f6892b14b5e51d59f66e1c79f3b9204','{"call_id":"c4","harness":"not_run","is_error":true,"text":"note was not run, because the run ended first."}');
INSERT INTO "events" VALUES(21,'sess-a',21,'reply_ready',1,'18656b1dec265a5e411cd227e6b2ed17757439ed561765be7e76d4e29c6cc665','id-0002',1,'2026-11-26T07:35:12.345Z',NULL,NULL,'{"kind":"notice","run_detail":"","run_reason":"cancelled","text":"Stopped."}');
INSERT INTO "events" VALUES(22,'sess-a',22,'run_end',1,'d24daa3e5a854dbee051cc8750c57bccb2b51d87d9729154140b0a55e6ef351c','id-0002',1,'2026-11-26T07:35:12.345Z',NULL,NULL,'{"detail":"","reason":"cancelled"}');
INSERT INTO "events" VALUES(23,'sess-a',23,'run_started',1,'9cfa77d3ef66bf3ae52b348c62ba2e4ce11d7704e2bdd19cbfe73c04fe9c6f8a','id-0003',NULL,'2026-11-26T07:35:12.345Z',NULL,'429dea44d348ac52fa43cd84436a5285dff967a3c46571f0982bac1380d02958','{"config_id":"cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc","trigger":"message"}');
INSERT INTO "events" VALUES(24,'sess-a',24,'user_message',1,'429dea44d348ac52fa43cd84436a5285dff967a3c46571f0982bac1380d02958','id-0003',NULL,'2026-11-26T07:35:12.345Z',NULL,NULL,'{"text":"Tick for ever."}');
INSERT INTO "events" VALUES(25,'sess-a',25,'model_request',1,'0f6466f2a82835a9d44b906d7acf0684b5f179b81b044d563f35582ce3d3808e','id-0003',1,'2026-11-26T07:35:12.345Z',NULL,NULL,'{"deadline_ms":600000}');
INSERT INTO "events" VALUES(26,'sess-a',26,'model_response',1,'1027fca0d9220716d5d95bc662edd57b9321463cddcf56f3d70f04d098a12fc3','id-0003',1,'2026-11-26T07:35:12.345Z',0,'0f6466f2a82835a9d44b906d7acf0684b5f179b81b044d563f35582ce3d3808e','{"cost_micro_dollars":800,"end":{"kind":"stop","model_reported":"made-up-1","provider_reason":"tool_calls","reason":"tool_calls","skipped":0},"message":{"parts":[{"arguments":{},"id":"c5","kind":"tool_call","name":"tick","parse_error":null,"raw_arguments":null}],"role":"assistant"},"usage":{"cache_read":0,"cache_write":0,"input_uncached":100,"output":20}}');
INSERT INTO "events" VALUES(27,'sess-a',27,'tool_intent',1,'5ea24d212736b3821c093d62e832bc094105a862df689b48707aceac3f8f972b','id-0003',1,'2026-11-26T07:35:12.345Z',NULL,'1027fca0d9220716d5d95bc662edd57b9321463cddcf56f3d70f04d098a12fc3','{"call_id":"c5","name":"tick"}');
INSERT INTO "events" VALUES(28,'sess-a',28,'tool_result',1,'d0e4dd8cbf67cbdfa45856a5fbdc10b28d5758f27559f2d4d284c46b24e99b5d','id-0003',1,'2026-11-26T07:35:12.345Z',0,'5ea24d212736b3821c093d62e832bc094105a862df689b48707aceac3f8f972b','{"call_id":"c5","is_error":false,"text":"Tick."}');
INSERT INTO "events" VALUES(29,'sess-a',29,'limit_hit',1,'85eb89d56b16ed697481d52ca1d9c75ae47ab9e81da3add00933f9355358914b','id-0003',1,'2026-11-26T07:35:12.345Z',NULL,NULL,'{"allowed":1,"limit":"steps","used":1}');
INSERT INTO "events" VALUES(30,'sess-a',30,'reply_ready',1,'878e4d71a276f4386bd63b092bfe03a836bf963d03aeb193240e28d9e8e974c5','id-0003',1,'2026-11-26T07:35:12.345Z',NULL,NULL,'{"kind":"notice","run_detail":"steps","run_reason":"limit","text":"The run stopped at its limit of 1 steps. The work so far is saved. Send a message to go on."}');
INSERT INTO "events" VALUES(31,'sess-a',31,'run_end',1,'c0aff3ffa6b3f11aa063920a5a2c324b35809f4105ffcbb8f16a46333b668555','id-0003',1,'2026-11-26T07:35:12.345Z',NULL,NULL,'{"detail":"steps","reason":"limit"}');
INSERT INTO "events" VALUES(32,'sess-b',1,'session_started',1,'e15e130b71d3c22ce674c29d3b6442ea033f2ee95efe60b8e550b87d472218fb',NULL,NULL,'2026-11-26T07:35:12.345Z',NULL,NULL,'{"channel":"terminal","conversation":"sess-b"}');
INSERT INTO "events" VALUES(33,'sess-b',2,'run_started',1,'aee48b30bb41572fb83c9208cb52b3688d30fd3ecc2f9180d489d670114fb0c9','id-0004',NULL,'2026-11-26T07:35:12.345Z',NULL,'c88eaec5ed7eb3a29d9ec81ed14c599ce09b9ab664426c9bccc5501e1cdceaac','{"config_id":"cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc","trigger":"message"}');
INSERT INTO "events" VALUES(34,'sess-b',3,'user_message',1,'c88eaec5ed7eb3a29d9ec81ed14c599ce09b9ab664426c9bccc5501e1cdceaac','id-0004',NULL,'2026-11-26T07:35:12.345Z',NULL,NULL,'{"text":"Wait a second."}');
INSERT INTO "events" VALUES(35,'sess-b',4,'model_request',1,'3e401bc8bf862aa124bf915b68907cc346cca8103c3532a2f598b0840d1ae144','id-0004',1,'2026-11-26T07:35:12.345Z',NULL,NULL,'{"deadline_ms":600000}');
INSERT INTO "events" VALUES(36,'sess-b',5,'model_response',1,'841bbcf8d6492c0b29a853e717b4cc17c1f276e79d32d97ae4e7bfe3151838fa','id-0004',1,'2026-11-26T07:35:12.345Z',0,'3e401bc8bf862aa124bf915b68907cc346cca8103c3532a2f598b0840d1ae144','{"cost_micro_dollars":800,"end":{"kind":"stop","model_reported":"made-up-1","provider_reason":"tool_calls","reason":"tool_calls","skipped":0},"message":{"parts":[{"arguments":{"seconds":1},"id":"c7","kind":"tool_call","name":"wait","parse_error":null,"raw_arguments":null}],"role":"assistant"},"usage":{"cache_read":0,"cache_write":0,"input_uncached":100,"output":20}}');
INSERT INTO "events" VALUES(37,'sess-b',6,'tool_intent',1,'8c408b66ae2d01efcc8e2ee5faf31615a3ca7047d7561f30538fcc55eeca0ebd','id-0004',1,'2026-11-26T07:35:12.345Z',NULL,'841bbcf8d6492c0b29a853e717b4cc17c1f276e79d32d97ae4e7bfe3151838fa','{"call_id":"c7","name":"wait"}');
INSERT INTO "events" VALUES(38,'sess-b',7,'tool_result',1,'b0747ce86239140c7059c10a8fb2e3a8f645bba197fd22d229cba9766ce49848','id-0004',1,'2026-11-26T07:35:12.345Z',NULL,'8c408b66ae2d01efcc8e2ee5faf31615a3ca7047d7561f30538fcc55eeca0ebd','{"call_id":"c7","harness":"interrupted","is_error":true,"text":"The harness was stopped while wait was running, and the call was not run again. Its outcome is unknown: it may have finished, partly run, or not run at all."}');
INSERT INTO "events" VALUES(39,'sess-b',8,'model_request',1,'672d121a8939681c87a0f4b8f37e944d4813f12ce82275372225fbf94b8a3fc0','id-0004',2,'2026-11-26T07:35:12.345Z',NULL,NULL,'{"deadline_ms":600000}');
INSERT INTO "events" VALUES(40,'sess-b',9,'model_response',1,'6831778b45daed391b5ec51b6e97edab27de0d095d29a8243b75880d43dc5d53','id-0004',2,'2026-11-26T07:35:12.345Z',0,'672d121a8939681c87a0f4b8f37e944d4813f12ce82275372225fbf94b8a3fc0','{"cost_micro_dollars":800,"end":{"kind":"stop","model_reported":"made-up-1","provider_reason":"end","reason":"end","skipped":0},"message":{"parts":[{"kind":"text","text":"Sorry."}],"role":"assistant"},"usage":{"cache_read":0,"cache_write":0,"input_uncached":100,"output":20}}');
INSERT INTO "events" VALUES(41,'sess-b',10,'reply_ready',1,'b2815752d9f8232d0c9e8cd39d667d438c4420d944123cdcfc39a825e41573bf','id-0004',2,'2026-11-26T07:35:12.345Z',NULL,NULL,'{"kind":"reply","run_detail":"","run_reason":"done","text":"Sorry."}');
INSERT INTO "events" VALUES(42,'sess-b',11,'run_end',1,'804fd576bcb05f66eea64c45dd910efd629ecab391a13759b67fbbfc8a20caf4','id-0004',2,'2026-11-26T07:35:12.345Z',NULL,NULL,'{"detail":"","reason":"done"}');
CREATE TABLE inbox (
  inbox_id           INTEGER PRIMARY KEY AUTOINCREMENT,
  channel            TEXT    NOT NULL,
  native_id          TEXT    NOT NULL,
  inbound            TEXT    NOT NULL,
  routed_session_id  TEXT,
  routed_seq         INTEGER,
  UNIQUE (channel, native_id)
) STRICT;
INSERT INTO "inbox" VALUES(1,'terminal','m1','{"answer":null,"approval_id":null,"attachments":[],"channel":"terminal","command":null,"conversation":"sess-a","kind":"message","native_id":"m1","received_at":"2026-11-26T07:35:12.345Z","sender":{"id":"local","verified":true},"sent_at":"2026-11-26T07:35:12.345Z","text":"Note one, and look up the weather.","v":1}','sess-a',3);
INSERT INTO "inbox" VALUES(2,'terminal','m2','{"answer":null,"approval_id":null,"attachments":[],"channel":"terminal","command":null,"conversation":"sess-a","kind":"message","native_id":"m2","received_at":"2026-11-26T07:35:12.345Z","sender":{"id":"local","verified":true},"sent_at":"2026-11-26T07:35:12.345Z","text":"Note two, then three.","v":1}','sess-a',14);
INSERT INTO "inbox" VALUES(3,'terminal','s1','{"answer":null,"approval_id":null,"attachments":[],"channel":"terminal","command":"stop","conversation":"sess-a","kind":"command","native_id":"s1","received_at":"2026-11-26T07:35:12.345Z","sender":{"id":"local","verified":true},"sent_at":"2026-11-26T07:35:12.345Z","text":"stop","v":1}','sess-a',21);
INSERT INTO "inbox" VALUES(4,'terminal','m3','{"answer":null,"approval_id":null,"attachments":[],"channel":"terminal","command":null,"conversation":"sess-a","kind":"message","native_id":"m3","received_at":"2026-11-26T07:35:12.345Z","sender":{"id":"local","verified":true},"sent_at":"2026-11-26T07:35:12.345Z","text":"Tick for ever.","v":1}','sess-a',24);
INSERT INTO "inbox" VALUES(5,'terminal','m4','{"answer":null,"approval_id":null,"attachments":[],"channel":"terminal","command":null,"conversation":"sess-b","kind":"message","native_id":"m4","received_at":"2026-11-26T07:35:12.345Z","sender":{"id":"local","verified":true},"sent_at":"2026-11-26T07:35:12.345Z","text":"Wait a second.","v":1}','sess-b',3);
CREATE TABLE outbox (
  outbox_id        INTEGER PRIMARY KEY AUTOINCREMENT,
  out_id           TEXT    NOT NULL UNIQUE,
  session_id       TEXT    NOT NULL,
  v                INTEGER NOT NULL CHECK (v >= 1),
  state            TEXT    NOT NULL,
  outbound         TEXT    NOT NULL,
  maybe_duplicate  INTEGER NOT NULL DEFAULT 0,
  receipt          TEXT,
  last_failure     TEXT,
  enqueued_at      TEXT    NOT NULL,
  sent_at          TEXT
) STRICT;
INSERT INTO "outbox" VALUES(1,'59240cda097f931917cdb71afedfb8ee4ec9ac89cd70eaeba15fb7c65573e505','sess-a',1,'sent','{"kind":"reply","maybe_duplicate":false,"out_id":"59240cda097f931917cdb71afedfb8ee4ec9ac89cd70eaeba15fb7c65573e505","session_id":"sess-a","text":"Noted one. There is no weather tool.","v":1}',0,'{"at":"2026-11-26T07:35:13.000Z"}',NULL,'2026-11-26T07:35:12.345Z','2026-11-26T07:35:12.345Z');
INSERT INTO "outbox" VALUES(2,'5c0390b7d3d0f2cea92d8a26ba56747eaf00aa85bb052847492833a25042d899','sess-a',1,'sending','{"kind":"notice","maybe_duplicate":false,"out_id":"5c0390b7d3d0f2cea92d8a26ba56747eaf00aa85bb052847492833a25042d899","session_id":"sess-a","text":"Stopped.","v":1}',0,NULL,NULL,'2026-11-26T07:35:12.345Z',NULL);
INSERT INTO "outbox" VALUES(3,'6c04b04e34f4def5a8e25e727561ecbb20037de414163cef87906c5d9be62426','sess-a',1,'pending','{"kind":"notice","maybe_duplicate":false,"out_id":"6c04b04e34f4def5a8e25e727561ecbb20037de414163cef87906c5d9be62426","session_id":"sess-a","text":"The run stopped at its limit of 1 steps. The work so far is saved. Send a message to go on.","v":1}',1,NULL,'{"cause":null,"detail":{},"effect":"unknown","kind":"timeout","message":"No answer.","retry_after_ms":null,"source":"t","v":1}','2026-11-26T07:35:12.345Z',NULL);
INSERT INTO "outbox" VALUES(4,'b2de3a36a2cd62e985d3bc3d91cecd143f7fd11b5699ce0f34a10efb13c7038a','sess-b',1,'pending','{"kind":"reply","maybe_duplicate":false,"out_id":"b2de3a36a2cd62e985d3bc3d91cecd143f7fd11b5699ce0f34a10efb13c7038a","session_id":"sess-b","text":"Sorry.","v":1}',0,NULL,NULL,'2026-11-26T07:35:12.345Z',NULL);
CREATE INDEX events_run_marks ON events (session_id, seq, type)
  WHERE type IN ('run_started', 'run_end');
CREATE INDEX effects_unresolved ON effects (effect_id)
  WHERE state IN ('intended', 'unknown');
CREATE INDEX inbox_pending ON inbox (inbox_id)
  WHERE routed_session_id IS NULL;
CREATE INDEX outbox_due ON outbox (outbox_id)
  WHERE state IN ('pending', 'sending');
DELETE FROM "sqlite_sequence";
INSERT INTO "sqlite_sequence" VALUES('inbox',5);
INSERT INTO "sqlite_sequence" VALUES('events',42);
INSERT INTO "sqlite_sequence" VALUES('outbox',4);
COMMIT;
