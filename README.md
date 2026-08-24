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

## Manifestator

Python-пакет `manifestator` создаёт manifest выпуска: сводит ролевые дорожки,
транскрибирует их локальной моделью через `transcribe.cpp`, чистит и суммаризирует
текст через Codex. Исходные WAV читаются из каталога в `episode.toml` и не изменяются:

```bash
cp episode.example.toml episode.toml
```

Установка Python-зависимостей:

```bash
uv sync
```

Весь pipeline запускается одной командой. После каждого этапа Manifestator просит
подтверждение перед продолжением, чтобы результат можно было проверить:

```bash
uv run manifestator
```

Готовые результаты переиспользуются. `--force` пересобирает их заново.

Этап транскрибации сначала выделяет речь на каждой ролевой дорожке, создаёт фрагменты не длиннее 24 секунд и держит модель транскрибации загруженной на протяжении всего прогона. Результаты сохраняются в `build/` и могут быть проверены до вызова Codex.

Следующий этап исправляет через `gpt-5.6-luna` ошибки распознавания и речевой мусор, сохраняя исходный транскрипт. Таймкоды и итоговый manifest используют очищенную версию. Результат не записывается, если Codex потерял или переставил сегменты либо данные не прошли Pydantic/JSON Schema validation.

После каждого успешного шага создаётся отчёт в `build/debug/`. Manifestator inspector открывается сочетанием `Alt+Shift+D` или `Ctrl+Shift+D`; этап очистки показывает все изменённые сегменты в формате «до / после».

Последний этап создаёт переносимый каталог из `output_dir`:

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
