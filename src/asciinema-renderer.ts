import type { ArtifactRenderer, RendererInstance } from "./types";

const CDN_BASE =
  "https://cdn.jsdelivr.net/npm/asciinema-player@3.17.0/dist/bundle";

interface AsciinemaController {
  getCurrentTime(): number;
  play(): Promise<boolean | void>;
  pause(): Promise<boolean | void>;
  seek(time: number): Promise<boolean | void>;
  dispose(): void;
}

interface AsciinemaPlayerApi {
  create(
    source: string | { data: string; parser: "asciicast" },
    container: HTMLElement,
    options: Record<string, unknown>,
  ): AsciinemaController;
}

declare global {
  interface Window {
    AsciinemaPlayer?: AsciinemaPlayerApi;
  }
}

let loadPromise: Promise<AsciinemaPlayerApi> | undefined;

function loadAsciinemaPlayer(): Promise<AsciinemaPlayerApi> {
  if (window.AsciinemaPlayer) {
    return Promise.resolve(window.AsciinemaPlayer);
  }
  if (loadPromise) {
    return loadPromise;
  }

  if (!document.querySelector("link[data-timekodik-asciinema]")) {
    const stylesheet = document.createElement("link");
    stylesheet.rel = "stylesheet";
    stylesheet.href = `${CDN_BASE}/asciinema-player.css`;
    stylesheet.dataset.timekodikAsciinema = "";
    document.head.append(stylesheet);
  }

  loadPromise = new Promise<AsciinemaPlayerApi>((resolve, reject) => {
    const script = document.createElement("script");
    script.src = `${CDN_BASE}/asciinema-player.min.js`;
    script.async = true;
    script.dataset.timekodikAsciinema = "";
    script.addEventListener("load", () => {
      if (window.AsciinemaPlayer) {
        resolve(window.AsciinemaPlayer);
      } else {
        reject(new Error("asciinema-player загрузился без browser API"));
      }
    });
    script.addEventListener("error", () => {
      reject(new Error("Не удалось загрузить asciinema-player с CDN"));
    });
    document.head.append(script);
  }).catch((error: unknown) => {
    loadPromise = undefined;
    throw error;
  });
  return loadPromise;
}

export const asciinemaRenderer: ArtifactRenderer = async ({
  artifact,
  container,
}) => {
  const source = artifact.source;
  if (!source?.url && !source?.data) {
    throw new Error(`Артефакт ${artifact.id} не содержит asciicast`);
  }

  const AsciinemaPlayer = await loadAsciinemaPlayer();
  const player = AsciinemaPlayer.create(
    source.data ? { data: source.data, parser: "asciicast" } : source.url ?? "",
    container,
    {
      autoPlay: false,
      fit: "width",
      idleTimeLimit: 2,
      preload: true,
      theme: "asciinema",
    },
  );

  const duration = artifact.endSeconds - artifact.startSeconds;
  let playingState = false;
  let positionedAtEnd = false;

  const instance: RendererInstance = {
    sync(currentTime, playing) {
      const localTime = Math.min(
        duration,
        Math.max(0, currentTime - artifact.startSeconds),
      );
      const atEnd = localTime >= duration - 0.05;

      if (
        Math.abs(localTime - player.getCurrentTime()) > 0.35 &&
        (!atEnd || !positionedAtEnd)
      ) {
        void player.seek(localTime);
      }
      positionedAtEnd = atEnd;

      const shouldPlay =
        playing &&
        currentTime >= artifact.startSeconds &&
        currentTime < artifact.endSeconds &&
        !atEnd;
      if (shouldPlay === playingState) {
        return;
      }
      playingState = shouldPlay;
      if (shouldPlay) {
        void player.play().then((started) => {
          if (started === false) {
            playingState = false;
          }
        });
      } else {
        void player.pause();
      }
    },
    destroy() {
      void player.pause();
      player.dispose();
      container.replaceChildren();
    },
  };

  return instance;
};
