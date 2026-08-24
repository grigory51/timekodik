import {
  activeInterval,
  parseManifest,
  resolveManifestUrls,
} from "./manifest";
import { seekAndPlay } from "./media";
import type {
  Artifact,
  ArtifactRenderer,
  EpisodeManifest,
  MountOptions,
  PodcastArtifactsInstance,
  RendererInstance,
} from "./types";

function resolveElement<T extends Element>(
  value: string | T,
  expectedType: { new (): T },
  label: string,
): T {
  const element = typeof value === "string" ? document.querySelector(value) : value;
  if (!(element instanceof expectedType)) {
    throw new Error(`${label} не найден`);
  }
  return element;
}

function formatTime(seconds: number): string {
  const totalSeconds = Math.max(0, Math.floor(seconds));
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const rest = totalSeconds % 60;
  return hours > 0
    ? `${hours}:${String(minutes).padStart(2, "0")}:${String(rest).padStart(2, "0")}`
    : `${minutes}:${String(rest).padStart(2, "0")}`;
}

function button(label: string, className: string): HTMLButtonElement {
  const element = document.createElement("button");
  element.type = "button";
  element.className = className;
  element.textContent = label;
  return element;
}

async function loadManifest(options: MountOptions): Promise<EpisodeManifest> {
  if (options.manifest) {
    return parseManifest(options.manifest);
  }
  if (!options.manifestUrl) {
    throw new Error("Нужно передать manifest или manifestUrl");
  }

  const response = await fetch(options.manifestUrl, { credentials: "omit" });
  if (!response.ok) {
    throw new Error(`Manifest не загружен: HTTP ${response.status}`);
  }
  return resolveManifestUrls(parseManifest(await response.json()), response.url);
}

export async function mountWidget(
  options: MountOptions,
  renderers: ReadonlyMap<string, ArtifactRenderer>,
): Promise<PodcastArtifactsInstance> {
  const media = resolveElement(options.media, HTMLMediaElement, "Аудиоплеер");
  const ownsContainer = options.container === undefined;
  const container = options.container
    ? resolveElement(options.container, HTMLElement, "Контейнер")
    : document.createElement("div");
  if (ownsContainer) {
    container.className = "timekodik";
    const anchor = options.after
      ? resolveElement(options.after, HTMLElement, "Точка вставки")
      : media;
    anchor.after(container);
  }
  container.replaceChildren();

  let manifest: EpisodeManifest;
  try {
    manifest = await loadManifest(options);
  } catch (error) {
    const message = document.createElement("p");
    message.className = "pa-widget__error";
    message.textContent =
      error instanceof Error ? error.message : "Manifest не загружен";
    container.append(message);
    throw error;
  }

  const root = document.createElement("section");
  root.className = "pa-widget";
  root.setAttribute("aria-label", "Навигация по выпуску");

  const chaptersSection = document.createElement("section");
  chaptersSection.className = "pa-widget__chapters";
  const chaptersHeading = document.createElement("h2");
  chaptersHeading.textContent = "Таймкоды";
  const chaptersList = document.createElement("ol");
  chaptersSection.append(chaptersHeading, chaptersList);

  const transcriptSection = document.createElement("details");
  transcriptSection.className = "pa-widget__transcript";
  const transcriptHeading = document.createElement("summary");
  transcriptHeading.textContent = "Транскрипт";
  const transcriptList = document.createElement("div");
  transcriptList.className = "pa-widget__transcript-list";
  transcriptSection.append(transcriptHeading, transcriptList);

  const artifactsSection = document.createElement("section");
  artifactsSection.className = "pa-widget__artifacts";
  const artifactsHeading = document.createElement("h3");
  artifactsHeading.textContent = "Материалы по ходу разговора";
  const artifactCards = document.createElement("div");
  artifactCards.className = "pa-widget__artifact-cards";
  artifactsSection.append(artifactsHeading, artifactCards);

  const dialog = document.createElement("dialog");
  dialog.className = "pa-widget__dialog";
  const dialogHeader = document.createElement("header");
  const dialogTitle = document.createElement("h3");
  const closeButton = button("Закрыть", "pa-widget__dialog-close");
  dialogHeader.append(dialogTitle, closeButton);
  const rendererContainer = document.createElement("div");
  rendererContainer.className = "pa-widget__renderer";
  dialog.append(dialogHeader, rendererContainer);

  root.append(chaptersSection, transcriptSection);
  if (manifest.artifacts.length > 0) {
    root.append(artifactsSection);
  }
  root.append(dialog);
  container.append(root);

  const chapterElements = new Map<string, HTMLElement>();
  for (const chapter of manifest.chapters) {
    const item = document.createElement("li");
    const seekButton = button("", "pa-widget__chapter-button");
    const time = document.createElement("time");
    time.textContent = formatTime(chapter.startSeconds);
    const title = document.createElement("span");
    title.textContent = chapter.title;
    seekButton.append(time, title);
    seekButton.addEventListener("click", () => {
      seekAndPlay(media, chapter.startSeconds);
    });
    item.append(seekButton);
    chaptersList.append(item);
    chapterElements.set(chapter.id, item);
  }

  const transcriptElements = new Map<string, HTMLElement>();
  for (const segment of manifest.transcript) {
    const row = document.createElement("article");
    row.className = "pa-widget__transcript-row";
    const seekButton = button(
      formatTime(segment.startSeconds),
      "pa-widget__time-button",
    );
    seekButton.addEventListener("click", () => {
      seekAndPlay(media, segment.startSeconds);
    });
    const copy = document.createElement("div");
    const speaker = document.createElement("strong");
    speaker.textContent = manifest.speakers[segment.speaker]?.name ?? segment.speaker;
    const text = document.createElement("p");
    text.textContent = segment.text;
    copy.append(speaker, text);
    row.append(seekButton, copy);
    transcriptList.append(row);
    transcriptElements.set(segment.id, row);
  }

  let rendererInstance: RendererInstance | undefined;
  let openArtifact: Artifact | undefined;

  const open = (artifact: Artifact): void => {
    const renderer = renderers.get(artifact.type);
    rendererInstance?.destroy();
    rendererInstance = undefined;
    rendererContainer.replaceChildren();
    dialogTitle.textContent = artifact.title;
    openArtifact = artifact;
    media.currentTime = artifact.startSeconds;
    if (!dialog.open) {
      dialog.showModal();
    }

    if (!renderer) {
      const error = document.createElement("p");
      error.className = "pa-widget__error";
      error.textContent = `Renderer ${artifact.type} не подключён`;
      rendererContainer.append(error);
    } else {
      try {
        const pendingRenderer = renderer({
          artifact,
          container: rendererContainer,
          media,
        });
        void Promise.resolve(pendingRenderer)
          .then((instance) => {
            if (openArtifact?.id !== artifact.id) {
              instance.destroy();
              return;
            }
            rendererInstance = instance;
            rendererInstance.sync(media.currentTime, !media.paused);
          })
          .catch((error: unknown) => {
            if (openArtifact?.id !== artifact.id) {
              return;
            }
            const message = document.createElement("p");
            message.className = "pa-widget__error";
            message.textContent =
              error instanceof Error ? error.message : "Артефакт не открыт";
            rendererContainer.append(message);
          });
      } catch (error) {
        const message = document.createElement("p");
        message.className = "pa-widget__error";
        message.textContent =
          error instanceof Error ? error.message : "Артефакт не открыт";
        rendererContainer.append(message);
      }
    }

    media
      .play()
      .then(() => {
        if (openArtifact?.id === artifact.id) {
          media.currentTime = artifact.startSeconds;
          rendererInstance?.sync(media.currentTime, true);
        }
      })
      .catch((error: unknown) => {
        const message = document.createElement("p");
        message.className = "pa-widget__error";
        message.textContent =
          error instanceof Error ? error.message : "Аудио не запустилось";
        rendererContainer.prepend(message);
      });

  };

  for (const artifact of manifest.artifacts) {
    const card = button("", "pa-widget__artifact-card");
    const time = document.createElement("span");
    time.textContent = `${formatTime(artifact.startSeconds)}–${formatTime(artifact.endSeconds)}`;
    const title = document.createElement("strong");
    title.textContent = artifact.title;
    card.append(time, title);
    card.addEventListener("click", () => open(artifact));
    artifactCards.append(card);
  }

  let activeChapterId: string | undefined;
  let activeTranscriptId: string | undefined;
  const sync = (): void => {
    const currentTime = media.currentTime;

    const chapter = activeInterval(manifest.chapters, currentTime);
    if (chapter?.id !== activeChapterId) {
      if (activeChapterId) {
        chapterElements.get(activeChapterId)?.removeAttribute("data-active");
      }
      if (chapter) {
        chapterElements.get(chapter.id)?.setAttribute("data-active", "true");
      }
      activeChapterId = chapter?.id;
    }

    const segment = activeInterval(manifest.transcript, currentTime);
    if (segment?.id !== activeTranscriptId) {
      if (activeTranscriptId) {
        transcriptElements.get(activeTranscriptId)?.removeAttribute("data-active");
      }
      if (segment) {
        transcriptElements.get(segment.id)?.setAttribute("data-active", "true");
      }
      activeTranscriptId = segment?.id;
    }

    if (openArtifact) {
      rendererInstance?.sync(currentTime, !media.paused);
    }
  };

  const close = (): void => {
    rendererInstance?.destroy();
    rendererInstance = undefined;
    openArtifact = undefined;
    if (dialog.open) {
      dialog.close();
    }
  };

  closeButton.addEventListener("click", close);
  dialog.addEventListener("cancel", close);
  const mediaEvents = [
    "loadedmetadata",
    "durationchange",
    "timeupdate",
    "play",
    "pause",
    "ratechange",
    "seeking",
    "seeked",
    "ended",
  ] as const;
  for (const event of mediaEvents) {
    media.addEventListener(event, sync);
  }
  sync();

  return {
    destroy() {
      close();
      for (const event of mediaEvents) {
        media.removeEventListener(event, sync);
      }
      if (ownsContainer) {
        container.remove();
      } else {
        container.replaceChildren();
      }
    },
  };
}
