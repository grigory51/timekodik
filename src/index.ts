import "./assets/styles.css";

import { readEmbedOptions } from "./core/embed";
import {
  activeInterval,
  parseManifest,
  resolveManifestUrls,
} from "./core/manifest";
import type {
  ArtifactRenderer,
  InitOptions,
  MountOptions,
  PodcastArtifactsInstance,
} from "./core/types";
import { mountWidget } from "./core/widget";
import {
  mountManifestatorInspector,
  parseManifestatorDebug,
} from "./debug/inspector";
import { asciinemaRenderer } from "./renderers/asciinema";
import { galleryRenderer } from "./renderers/gallery";

const renderers = new Map<string, ArtifactRenderer>([
  ["asciinema", asciinemaRenderer],
  ["gallery", galleryRenderer],
]);
const embedScript =
  typeof document === "undefined"
    ? null
    : (document.currentScript as HTMLScriptElement | null);

export function registerRenderer(type: string, renderer: ArtifactRenderer): void {
  if (!type) {
    throw new Error("Renderer type не может быть пустым");
  }
  renderers.set(type, renderer);
}

export async function mount(
  options: MountOptions,
): Promise<PodcastArtifactsInstance> {
  return mountWidget(options, renderers);
}

export async function init(
  options: InitOptions,
): Promise<PodcastArtifactsInstance> {
  return mountWidget(
    {
      media: options.audio,
      after: options.after,
      ...(typeof options.manifest === "string"
        ? { manifestUrl: options.manifest }
        : { manifest: options.manifest }),
    },
    renderers,
  );
}

function autoInit(): void {
  if (!embedScript) {
    return;
  }

  let options: ReturnType<typeof readEmbedOptions>;
  try {
    options = readEmbedOptions(embedScript.dataset);
  } catch (error) {
    console.error(error);
    return;
  }
  if (!options) {
    return;
  }

  const start = (): void => {
    void init(options).catch((error: unknown) => {
      console.error("timekodik не запустился", error);
    });
    const debug = (window as typeof window & {
      TIMEKODIK_MANIFESTATOR_DEBUG?: unknown;
    }).TIMEKODIK_MANIFESTATOR_DEBUG;
    if (debug) {
      mountManifestatorInspector(debug);
    }
  };
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start, { once: true });
  } else {
    start();
  }
}

autoInit();

export {
  activeInterval,
  mountManifestatorInspector,
  parseManifest,
  parseManifestatorDebug,
  readEmbedOptions,
  resolveManifestUrls,
};
export type * from "./core/types";
export type * from "./debug/types";
