import assert from "node:assert/strict";
import test from "node:test";

import {
  activeInterval,
  parseManifest,
  resolveManifestUrls,
} from "../src/manifest.ts";
import { parseManifestatorDebug } from "../src/debug-inspector.ts";
import { readEmbedOptions } from "../src/embed.ts";
import { seekAndPlay } from "../src/media.ts";

const manifest = {
  schemaVersion: 1,
  speakers: { host: { name: "Host" } },
  transcript: [
    { id: "s1", speaker: "host", startSeconds: 0, endSeconds: 10, text: "Text" },
  ],
  chapters: [
    { id: "c1", startSeconds: 0, endSeconds: 50, title: "Chapter", summary: "" },
  ],
  artifacts: [
    { id: "a1", type: "asciinema", startSeconds: 25, endSeconds: 35, title: "Terminal", source: { url: "terminal.cast" } },
  ],
};

test("manifest intervals map to the active item", () => {
  const parsed = parseManifest(manifest);
  assert.equal(activeInterval(parsed.artifacts, 30)?.id, "a1");
  assert.equal(activeInterval(parsed.artifacts, 40), undefined);
});

test("invalid intervals are rejected", () => {
  assert.throws(
    () => parseManifest({ ...manifest, artifacts: [{ ...manifest.artifacts[0], endSeconds: 20 }] }),
    /временные данные/,
  );
});

test("manifest file URLs resolve relative to the manifest", () => {
  const resolved = resolveManifestUrls(
    parseManifest({
      ...manifest,
      artifacts: [
        { ...manifest.artifacts[0], source: { url: "terminal.cast" } },
      ],
    }),
    "http://127.0.0.1:8000/addon/data/episode/manifest.json",
  );
  assert.equal(
    resolved.artifacts[0]?.source?.url,
    "http://127.0.0.1:8000/addon/data/episode/terminal.cast",
  );
});

test("manifestator debug report validates stage status", () => {
  const report = {
    schemaVersion: 1,
    episodeId: "episode",
    stages: [
      {
        schemaVersion: 1,
        episodeId: "episode",
        stage: "mix",
        label: "Сведение",
        status: "complete",
        data: { durationSeconds: 100 },
      },
    ],
  };
  assert.equal(parseManifestatorDebug(report).stages[0]?.stage, "mix");
  assert.throws(
    () => parseManifestatorDebug({ ...report, stages: [{ ...report.stages[0], status: "broken" }] }),
    /Некорректный этап/,
  );
});

test("timecode seeks and starts playback", () => {
  let played = false;
  const media = {
    currentTime: 0,
    play: async () => {
      played = true;
    },
  };

  seekAndPlay(media, 42);

  assert.equal(media.currentTime, 42);
  assert.equal(played, true);
});

test("embed requires only audio and manifest", () => {
  assert.deepEqual(
    readEmbedOptions({ audio: "#audio", manifest: "episode.json" }),
    { audio: "#audio", manifest: "episode.json" },
  );
  assert.throws(
    () => readEmbedOptions({ audio: "#audio" }),
    /data-audio и data-manifest обязательны/,
  );
});
