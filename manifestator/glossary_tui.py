from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import click
from rich.markup import escape
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Footer, Header, Input, Label, ListItem, ListView, Static

from .common import ROOT, atomic_json, process_lock
from .config import EpisodeConfig, config_for_source, load_config
from .models import GlossaryCandidate, GlossaryDocument, GlossaryStatus
from .review import load_glossary, merge_glossary, whisper_hypotheses
from .transcript import load_transcript


class GlossaryApp(App[None]):
    """Редактор сомнительных терминов, найденных при проверке transcript."""

    TITLE = "Manifestator — словарь"
    BINDINGS = [
        ("escape", "quit", "Выйти"),
    ]
    CSS = """
    Screen { layout: vertical; }
    #stats { height: auto; padding: 0 1; }
    #workspace { height: 1fr; }
    #candidates { width: 38%; border-right: solid $primary; }
    #editor { width: 62%; padding: 1 2; }
    #details { height: 1fr; overflow-y: auto; }
    #preferred { margin-top: 1; }
    #actions { height: auto; margin-top: 1; }
    #actions Button { margin-right: 1; }
    .resolved { display: none; }
    ListItem { height: auto; padding: 0 1; }
    """

    def __init__(
        self,
        document: GlossaryDocument,
        evidence: dict[str, tuple[str, str]],
        save: Callable[[GlossaryDocument], None],
    ) -> None:
        super().__init__()
        self.candidates = sorted(
            (
                candidate.model_copy(
                    update={"status": "confirmed"}
                    if candidate.preferred and candidate.status == "pending"
                    else {}
                )
                for candidate in document.candidates
            ),
            key=lambda candidate: candidate.status != "pending",
        )
        self.evidence = evidence
        self.save = save
        self.selected_index = 0

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(self._stats_text(), id="stats")
        with Horizontal(id="workspace"):
            yield ListView(
                *(
                    ListItem(
                        Label(self._candidate_label(candidate)),
                        classes=(
                            "resolved" if candidate.status != "pending" else ""
                        ),
                        disabled=candidate.status != "pending",
                    )
                    for candidate in self.candidates
                ),
                id="candidates",
            )
            with Vertical(id="editor"):
                yield Static(id="details", markup=False)
                yield Input(placeholder="Правильное написание", id="preferred")
                with Horizontal(id="actions"):
                    yield Button("Подтвердить", id="confirm", variant="primary")
                    yield Button("Игнорировать", id="ignore", variant="warning")
        yield Footer()

    def on_mount(self) -> None:
        if not any(candidate.status == "pending" for candidate in self.candidates):
            self._show_complete()
            return
        self.query_one("#candidates", ListView).index = 0
        self._show_candidate(0)

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        if event.list_view.index is not None:
            self._show_candidate(event.list_view.index)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        self._show_candidate(event.index)
        self.query_one("#preferred", Input).focus()

    def on_input_submitted(self, _event: Input.Submitted) -> None:
        self._confirm_current()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "confirm":
            self._confirm_current()
        elif event.button.id == "ignore":
            self._set_current(status="ignored", preferred=None)

    def _confirm_current(self) -> None:
        preferred = self.query_one("#preferred", Input).value.strip()
        if not preferred:
            self.notify("Введите правильное написание", severity="error")
            return
        self._set_current(status="confirmed", preferred=preferred)

    def _set_current(
        self,
        *,
        status: GlossaryStatus,
        preferred: str | None,
    ) -> None:
        candidate = self.candidates[self.selected_index].model_copy(
            update={"status": status, "preferred": preferred}
        )
        self.candidates[self.selected_index] = candidate
        self.save(GlossaryDocument(candidates=self.candidates))
        self.query_one("#stats", Static).update(self._stats_text())
        item = self.query_one("#candidates", ListView).children[self.selected_index]
        item.query_one(Label).update(self._candidate_label(candidate))
        item.disabled = True
        item.add_class("resolved")
        self._select_next_pending()

    def _select_next_pending(self) -> None:
        for offset in range(1, len(self.candidates) + 1):
            index = (self.selected_index + offset) % len(self.candidates)
            if self.candidates[index].status == "pending":
                self.query_one("#candidates", ListView).index = index
                self._show_candidate(index)
                return
        self._show_complete()

    def _show_complete(self) -> None:
        self.query_one("#details", Static).update("Все термины размечены")
        self.query_one("#preferred", Input).disabled = True
        for button in self.query(Button):
            button.disabled = True
        self.notify("Неразмеченных терминов больше нет")

    def _stats_text(self) -> str:
        pending = sum(candidate.status == "pending" for candidate in self.candidates)
        confirmed = sum(
            candidate.status == "confirmed" for candidate in self.candidates
        )
        ignored = sum(candidate.status == "ignored" for candidate in self.candidates)
        return (
            f"Осталось: {pending}  ·  "
            f"Подтверждено: {confirmed}  ·  Игнорировано: {ignored}"
        )

    def _show_candidate(self, index: int) -> None:
        self.selected_index = index
        candidate = self.candidates[index]
        hypotheses = [
            self.evidence[segment_id]
            for segment_id in candidate.segmentIds
            if segment_id in self.evidence
        ]
        gigaam = "\n".join(dict.fromkeys(value[0] for value in hypotheses)) or "—"
        whisper = "\n".join(dict.fromkeys(value[1] for value in hypotheses)) or "—"
        self.query_one("#details", Static).update(
            f"Услышано: {candidate.heard}\n"
            f"Предложено: {candidate.suggested}\n\n"
            f"GigaAM:\n{gigaam}\n\n"
            f"Whisper:\n{whisper}\n\n"
            f"Контекст:\n{candidate.context}"
        )
        self.query_one("#preferred", Input).value = (
            candidate.preferred or candidate.suggested
        )

    @staticmethod
    def _candidate_label(candidate: GlossaryCandidate) -> str:
        marker = {"pending": "?", "confirmed": "✓", "ignored": "×"}[
            candidate.status
        ]
        result = f"{marker} {candidate.heard}"
        if candidate.preferred:
            result += f" → {candidate.preferred}"
        return escape(result)


@click.command()
@click.argument(
    "source_path",
    required=False,
    type=click.Path(path_type=Path, exists=True),
)
@click.option(
    "--config",
    "config_path",
    type=click.Path(path_type=Path, dir_okay=False),
    help="Использовать расширенный TOML-конфиг.",
)
def glossary_cli(source_path: Path | None, config_path: Path | None) -> None:
    """Разметить сомнительные термины из готового transcript."""
    if source_path and config_path:
        raise click.UsageError("Передайте SOURCE_PATH или --config, но не оба сразу")
    if source_path:
        config = config_for_source(source_path)
    else:
        config_path = config_path or ROOT / "episode.toml"
        if not config_path.is_file():
            raise click.UsageError("Передайте исходный аудиофайл, каталог или --config")
        config = load_config(config_path)

    if not config.glossary_output.is_file():
        raise click.ClickException(
            "Сначала запустите manifestator, чтобы получить саджесты"
        )
    if not load_glossary(config.glossary_output).candidates:
        click.echo("Саджестов нет")
        return
    with process_lock(config.process_lock):
        if edit_glossary(config):
            click.echo(f"Словарь сохранён: {config.glossary_corpus_output}")


def edit_glossary(config: EpisodeConfig) -> bool:
    source_document = load_glossary(config.glossary_output)
    document = merge_glossary(
        GlossaryDocument(candidates=[]),
        source_document.candidates,
    )
    changed = False

    def save(result: GlossaryDocument) -> None:
        nonlocal changed
        atomic_json(config.glossary_output, result)
        corpus = load_glossary(config.glossary_corpus_output)
        updated_corpus = update_glossary_corpus(corpus, result.candidates)
        if updated_corpus != corpus:
            atomic_json(config.glossary_corpus_output, updated_corpus)
        changed = True

    if document != source_document:
        save(document)
    GlossaryApp(document, _load_evidence(config), save).run()
    return changed


def _load_evidence(config: EpisodeConfig) -> dict[str, tuple[str, str]]:
    evidence: dict[str, tuple[str, str]] = {}
    transcript_pairs = [
        (config.transcript_output, config.whisper_transcript_output),
        (config.final_transcript_output, config.final_whisper_transcript_output),
    ]
    for primary_path, whisper_path in transcript_pairs:
        if not primary_path.is_file() or not whisper_path.is_file():
            continue
        primary = load_transcript(primary_path)
        whisper = load_transcript(whisper_path)
        hypotheses = whisper_hypotheses(primary.segments, whisper.segments)
        evidence.update(
            {
                segment.id: (segment.text, hypotheses[segment.id])
                for segment in primary.segments
            }
        )
    return evidence


def update_glossary_corpus(
    corpus: GlossaryDocument,
    reviewed: list[GlossaryCandidate],
) -> GlossaryDocument:
    decisions = {
        candidate.heard.strip().casefold(): candidate
        for candidate in reviewed
        if candidate.status != "pending"
    }
    return GlossaryDocument(
        candidates=[
            candidate
            for candidate in corpus.candidates
            if candidate.heard.strip().casefold() not in decisions
        ]
        + [
            candidate
            for candidate in decisions.values()
            if candidate.status == "confirmed" and candidate.preferred
        ]
    )
