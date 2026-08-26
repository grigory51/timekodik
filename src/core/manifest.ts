import type {
  Artifact,
  Chapter,
  EpisodeManifest,
  TranscriptSegment,
} from "./types";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isString(value: unknown): value is string {
  return typeof value === "string" && value.length > 0;
}

function isSecond(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value) && value >= 0;
}

function hasInterval(value: Record<string, unknown>): boolean {
  return (
    isSecond(value.startSeconds) &&
    isSecond(value.endSeconds) &&
    value.endSeconds >= value.startSeconds
  );
}

function isTranscriptSegment(value: unknown): value is TranscriptSegment {
  return (
    isRecord(value) &&
    isString(value.id) &&
    isString(value.speaker) &&
    isString(value.text) &&
    hasInterval(value)
  );
}

function isChapter(value: unknown): value is Chapter {
  return (
    isRecord(value) &&
    isString(value.id) &&
    isString(value.title) &&
    typeof value.summary === "string" &&
    hasInterval(value)
  );
}

function isArtifact(value: unknown): value is Artifact {
  if (
    !isRecord(value) ||
    !isString(value.id) ||
    !isString(value.type) ||
    !isString(value.title) ||
    !hasInterval(value)
  ) {
    return false;
  }

  if (value.source === undefined) {
    return value.type !== "gallery";
  }

  if (!isRecord(value.source)) {
    return false;
  }
  const urls = value.source.urls;
  const hasUrls =
    Array.isArray(urls) && urls.length > 0 && urls.every(isString);
  return (
    (value.source.url === undefined || isString(value.source.url)) &&
    (value.source.data === undefined || isString(value.source.data)) &&
    (urls === undefined || hasUrls) &&
    (value.type !== "gallery" || hasUrls) &&
    (isString(value.source.url) || isString(value.source.data) || hasUrls)
  );
}

export function parseManifest(value: unknown): EpisodeManifest {
  if (!isRecord(value) || value.schemaVersion !== 1) {
    throw new Error("Manifest должен иметь schemaVersion: 1");
  }

  if (
    !isRecord(value.speakers) ||
    !Array.isArray(value.transcript) ||
    !value.transcript.every(isTranscriptSegment) ||
    !Array.isArray(value.chapters) ||
    !value.chapters.every(isChapter) ||
    !Array.isArray(value.artifacts) ||
    !value.artifacts.every(isArtifact)
  ) {
    throw new Error("Manifest содержит некорректные временные данные");
  }

  for (const [speakerId, speaker] of Object.entries(value.speakers)) {
    if (!isString(speakerId) || !isRecord(speaker) || !isString(speaker.name)) {
      throw new Error("Manifest содержит некорректного спикера");
    }
  }

  return value as unknown as EpisodeManifest;
}

export function resolveManifestUrls(
  manifest: EpisodeManifest,
  manifestUrl: string,
): EpisodeManifest {
  return {
    ...manifest,
    artifacts: manifest.artifacts.map((artifact) => ({
      ...artifact,
      source: artifact.source
        ? {
            ...artifact.source,
            ...(artifact.source.url
              ? { url: new URL(artifact.source.url, manifestUrl).href }
              : {}),
            ...(artifact.source.urls
              ? {
                  urls: artifact.source.urls.map(
                    (url) => new URL(url, manifestUrl).href,
                  ),
                }
              : {}),
          }
        : undefined,
    })),
  };
}

export function activeInterval<T extends { startSeconds: number; endSeconds: number }>(
  values: T[],
  currentTime: number,
): T | undefined {
  let active: T | undefined;
  for (const value of values) {
    if (
      currentTime >= value.startSeconds &&
      currentTime < value.endSeconds &&
      (!active || value.startSeconds > active.startSeconds)
    ) {
      active = value;
    }
  }
  return active;
}
