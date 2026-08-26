import assert from "node:assert/strict";
import test from "node:test";

import {
  activeInterval,
  parseManifest,
  resolveManifestUrls,
} from "../src/core/manifest.ts";
import { readEmbedOptions } from "../src/core/embed.ts";
import { seekAndPlay } from "../src/core/media.ts";
import { parseManifestatorDebug } from "../src/debug/inspector.ts";

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

test("latest interval wins when transcript segments overlap", () => {
  const segments = [
    { id: "earlier", startSeconds: 106.67, endSeconds: 123.106 },
    { id: "later", startSeconds: 117.282, endSeconds: 129.35 },
  ];

  assert.equal(activeInterval(segments, 119.52)?.id, "later");
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

test("gallery image URLs resolve relative to the manifest", () => {
  const resolved = resolveManifestUrls(
    parseManifest({
      ...manifest,
      artifacts: [
        {
          ...manifest.artifacts[0],
          type: "gallery",
          source: {
            url: "photos/one.jpg",
            urls: ["photos/one.jpg", "photos/two.jpg"],
          },
        },
      ],
    }),
    "http://127.0.0.1:8000/episode/manifest.json",
  );
  assert.deepEqual(resolved.artifacts[0]?.source?.urls, [
    "http://127.0.0.1:8000/episode/photos/one.jpg",
    "http://127.0.0.1:8000/episode/photos/two.jpg",
  ]);
  assert.equal(
    resolved.artifacts[0]?.source?.url,
    "http://127.0.0.1:8000/episode/photos/one.jpg",
  );
  assert.throws(
    () =>
      parseManifest({
        ...manifest,
        artifacts: [{ ...manifest.artifacts[0], type: "gallery", source: { urls: [] } }],
      }),
    /временные данные/,
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
