import type {
  ManifestatorDebugInstance,
  ManifestatorDebugReport,
  ManifestatorDebugStage,
} from "./types";


function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function parseManifestatorDebug(value: unknown): ManifestatorDebugReport {
  if (
    !isRecord(value) ||
    value.schemaVersion !== 1 ||
    typeof value.episodeId !== "string" ||
    !Array.isArray(value.stages)
  ) {
    throw new Error("Некорректный manifestator debug report");
  }
  for (const stage of value.stages) {
    if (
      !isRecord(stage) ||
      stage.schemaVersion !== 1 ||
      typeof stage.episodeId !== "string" ||
      typeof stage.stage !== "string" ||
      typeof stage.label !== "string" ||
      (stage.status !== "complete" && stage.status !== "pending") ||
      (stage.data !== undefined && !isRecord(stage.data))
    ) {
      throw new Error("Некорректный этап manifestator debug report");
    }
  }
  return value as unknown as ManifestatorDebugReport;
}

function element<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  className?: string,
  text?: string,
): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (className) {
    node.className = className;
  }
  if (text !== undefined) {
    node.textContent = text;
  }
  return node;
}

function formatTime(seconds: number): string {
  const rounded = Math.max(0, Math.floor(seconds));
  return `${String(Math.floor(rounded / 3600)).padStart(2, "0")}:${String(
    Math.floor((rounded % 3600) / 60),
  ).padStart(2, "0")}:${String(rounded % 60).padStart(2, "0")}`;
}

function transcriptChanges(data: Record<string, unknown>): Record<string, unknown>[] {
  if (!Array.isArray(data.changes)) {
    return [];
  }
  return data.changes.filter(isRecord);
}

export function mountManifestatorInspector(
  value: unknown,
): ManifestatorDebugInstance {
  const report = parseManifestatorDebug(value);
  const dialog = element("dialog", "pa-debug-dialog");
  const header = element("header", "pa-debug-dialog__header");
  const headingGroup = element("div");
  const eyebrow = element("span", "pa-debug-dialog__eyebrow", report.episodeId);
  const heading = element("h2", "", "Manifestator inspector");
  headingGroup.append(eyebrow, heading);
  const closeButton = element("button", "pa-debug-dialog__close", "Закрыть");
  closeButton.type = "button";
  header.append(headingGroup, closeButton);

  const layout = element("div", "pa-debug-dialog__layout");
  const navigation = element("nav", "pa-debug-dialog__stages");
  navigation.setAttribute("aria-label", "Этапы manifestator");
  const detail = element("section", "pa-debug-dialog__detail");
  layout.append(navigation, detail);
  dialog.append(header, layout);
  document.body.append(dialog);

  const stageButtons = new Map<string, HTMLButtonElement>();
  const render = (stage: ManifestatorDebugStage): void => {
    for (const button of stageButtons.values()) {
      button.removeAttribute("data-active");
    }
    stageButtons.get(stage.stage)?.setAttribute("data-active", "true");
    detail.replaceChildren();

    const titleRow = element("header", "pa-debug-dialog__detail-header");
    const title = element("h3", "", stage.label);
    const status = element(
      "span",
      `pa-debug-status pa-debug-status--${stage.status}`,
      stage.status === "complete" ? "готово" : "ожидает",
    );
    titleRow.append(title, status);
    detail.append(titleRow);

    if (stage.status === "pending" || !stage.data) {
      detail.append(
        element(
          "p",
          "pa-debug-dialog__empty",
          "Этап ещё не запускался. После успешного выполнения здесь появится отчёт.",
        ),
      );
      return;
    }

    if (stage.stage === "clean-transcript") {
      const changed =
        typeof stage.data.changed === "number" ? stage.data.changed : 0;
      const unchanged =
        typeof stage.data.unchanged === "number" ? stage.data.unchanged : 0;
      const stats = element("div", "pa-debug-diff__stats");
      stats.append(
        element("strong", "", `${changed} изменено`),
        element("span", "", `${unchanged} без изменений`),
      );
      detail.append(stats);

      const changes = transcriptChanges(stage.data);
      const list = element("div", "pa-debug-diff");
      for (const change of changes) {
        const item = element("article", "pa-debug-diff__item");
        const meta = element("header");
        const seconds =
          typeof change.startSeconds === "number" ? change.startSeconds : 0;
        meta.append(
          element("time", "", formatTime(seconds)),
          element(
            "strong",
            "",
            typeof change.speaker === "string" ? change.speaker : "speaker",
          ),
        );
        const before = element(
          "del",
          "",
          typeof change.before === "string" ? change.before : "",
        );
        const after = element(
          "ins",
          "",
          typeof change.after === "string" ? change.after : "",
        );
        item.append(meta, before, after);
        list.append(item);
      }
      if (changes.length === 0) {
        list.append(
          element("p", "pa-debug-dialog__empty", "Изменений в тексте нет."),
        );
      }
      detail.append(list);
      return;
    }

    const raw = element("pre", "pa-debug-dialog__json");
    raw.textContent = JSON.stringify(stage.data, null, 2);
    detail.append(raw);
  };

  for (const [index, stage] of report.stages.entries()) {
    const button = element("button", "pa-debug-stage") as HTMLButtonElement;
    button.type = "button";
    button.append(
      element("span", "pa-debug-stage__index", String(index + 1).padStart(2, "0")),
      element("span", "pa-debug-stage__label", stage.label),
      element(
        "span",
        `pa-debug-stage__dot pa-debug-stage__dot--${stage.status}`,
      ),
    );
    button.addEventListener("click", () => render(stage));
    navigation.append(button);
    stageButtons.set(stage.stage, button);
  }

  const initial = [...report.stages]
    .reverse()
    .find((stage) => stage.status === "complete") ?? report.stages[0];
  if (initial) {
    render(initial);
  }

  const open = (): void => {
    if (!dialog.open) {
      dialog.showModal();
    }
  };
  const close = (): void => dialog.close();
  const hotkey = (event: KeyboardEvent): void => {
    const target = event.target;
    const modifier = event.altKey || event.ctrlKey;
    if (
      !(modifier && event.shiftKey && event.code === "KeyD") ||
      (target instanceof HTMLElement &&
        (target.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName)))
    ) {
      return;
    }
    event.preventDefault();
    event.stopPropagation();
    dialog.open ? close() : open();
  };
  closeButton.addEventListener("click", close);
  window.addEventListener("keydown", hotkey, true);

  return {
    open,
    destroy() {
      window.removeEventListener("keydown", hotkey, true);
      dialog.remove();
    },
  };
}
