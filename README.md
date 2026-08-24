# timekodik

Прикрутить таймкоды к `<audio>` на существующей странице.

Standalone TypeScript-библиотека добавляет к нативному аудиоплееру кликабельные
главы, транскрипт и синхронные артефакты, не заменяя сам плеер.

## Установка

Опубликуйте manifest выпуска и вставьте один тег:

```html
<script
  defer
  src="https://cdn.jsdelivr.net/npm/timekodik@0.1.0/dist/addon/timekodik.js"
  data-audio="#audio-player"
  data-manifest="https://cdn.example/episodes/example/manifest.json"
></script>
```

Библиотека сама загрузит лежащий рядом `timekodik.css` и вставит интерфейс сразу
после `<audio>`. `data-after=".audio-block"` меняет точку вставки.

## API библиотеки

```ts
const instance = await Timekodik.init({
  audio: "#audio-player",
  manifest: "https://cdn.example/episodes/example/manifest.json",
});

instance.destroy();
```

Для нового типа артефакта регистрируется renderer:

```ts
Timekodik.registerRenderer("diagram", ({ artifact, container, media }) => ({
  sync(currentTime, playing) {},
  destroy() {},
}));
```

Renderer `asciinema` при первом открытии артефакта загружает standalone player и
его CSS с jsDelivr. Podcast audio остаётся master clock; терминал следует за
`play`, `pause` и `seek` основного плеера.

## Сборка frontend

```bash
npm install
npm run check
```

Результат:

- `dist/addon/timekodik.js` — standalone IIFE;
- `dist/addon/timekodik.css` — стили, которые IIFE подключает автоматически.

## Релизы

Версия релиза берётся из git-тега. Из чистого `main`:

```bash
make release              # patch
make release BUMP=minor
make release BUMP=major
```

Тег `v*` запускает `.github/workflows/release.yml`: workflow подставляет версию
тега в package metadata, проверяет сборку, публикует пакет на npm и создаёт GitHub
Release. После публикации та же версия сразу доступна через jsDelivr.

Для первой публикации нужен временный repository secret `NPM_TOKEN`. После появления
пакета настройте для `grigory51/timekodik` trusted publisher `release.yml` с правом
`npm publish`, затем удалите secret. Последующие релизы используют GitHub OIDC.

## Manifestator

Python-пакет `manifestator` создаёт manifest выпуска: сводит ролевые дорожки,
транскрибирует их локальной моделью через `transcribe.cpp`, чистит и суммаризирует
текст через Codex. Исходные WAV читаются из каталога в `episode.toml` и не изменяются:

```bash
cp episode.example.toml episode.toml
```

Установка Python-зависимостей и официального Metal provider `transcribe.cpp v0.2.1`:

```bash
uv sync
uv run python -m manifestator doctor
```

Команды выполняются поэтапно:

```bash
uv run python -m manifestator mix
uv run python -m manifestator transcribe
uv run python -m manifestator clean-transcript
uv run python -m manifestator summarize
uv run python -m manifestator build-manifest
```

`transcribe` сначала выделяет речь на каждой ролевой дорожке, создаёт фрагменты не длиннее 24 секунд и держит модель транскрибации загруженной на протяжении всего прогона. Результаты сохраняются в `build/` и могут быть проверены до вызова Codex.

`clean-transcript` исправляет через `gpt-5.6-luna` ошибки распознавания и речевой мусор, сохраняя исходный транскрипт. `summarize` и `build-manifest` используют очищенную версию. Результат не записывается, если Codex потерял или переставил сегменты либо данные не прошли Pydantic/JSON Schema validation.

После каждого успешного шага создаётся отчёт в `build/debug/`. Manifestator inspector открывается сочетанием `Alt+Shift+D` или `Ctrl+Shift+D`; этап очистки показывает все изменённые сегменты в формате «до / после».

`build-manifest` создаёт переносимый каталог из `output_dir`:

```text
output/example/
├── manifest.json
├── audio.mp3
└── artifacts/
    └── terminal-session.cast
```

Промежуточные аудио и JSON сохраняются автоматически в `build/`; задавать пути
к ним в конфиге не нужно.

URL файлов артефактов в manifest разрешаются относительно самого `manifest.json`; содержимое `.cast` в JSON или JavaScript не встраивается.

Перед публикацией JSON и `.cast` на другом origin нужно разрешить CORS для сайта
и отдавать корректные `Content-Type` (`application/json` и `application/x-asciicast`).
