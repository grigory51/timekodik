# timekodik

Прикрутить таймкоды к `<audio>` на существующей странице.

Standalone TypeScript-библиотека добавляет к нативному аудиоплееру кликабельные
главы, транскрипт и синхронные артефакты, не заменяя сам плеер.

## Установка

Опубликуйте manifest выпуска, подключите стили и script:

```html
<link
  rel="stylesheet"
  href="https://cdn.jsdelivr.net/npm/@grigory51/timekodik@0.1.1/dist/addon/timekodik.css"
>
<script
  defer
  src="https://cdn.jsdelivr.net/npm/@grigory51/timekodik@0.1.1/dist/addon/timekodik.js"
  data-audio="#audio-player"
  data-manifest="https://cdn.example/episodes/example/manifest.json"
></script>
```

Библиотека вставит интерфейс сразу после `<audio>`. `data-after=".audio-block"`
меняет точку вставки.

## Свои стили

Чтобы оформить виджет под свой сайт, не подключайте `timekodik.css` и определите
нужные стили самостоятельно. Библиотека никогда не добавляет свой CSS в страницу.

Публичные классы разметки:

- `.timekodik` — внешний контейнер;
- `.pa-widget` — корень виджета;
- `.pa-widget__chapters`, `.pa-widget__chapter-button` — таймкоды;
- `.pa-widget__transcript`, `.pa-widget__transcript-list`, `.pa-widget__transcript-row`, `.pa-widget__time-button` — транскрипт;
- `.pa-widget__artifacts`, `.pa-widget__artifact-cards`, `.pa-widget__artifact-card` — артефакты;
- `.pa-widget__dialog`, `.pa-widget__dialog-close`, `.pa-widget__renderer` — окно артефакта;
- `.pa-widget__error` — сообщение об ошибке.

Активная глава и активная строка транскрипта получают `data-active="true"`:

```css
.pa-widget__chapters li[data-active="true"] .pa-widget__chapter-button,
.pa-widget__transcript-row[data-active="true"] {
  /* Текущий фрагмент */
}
```

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
- `dist/addon/timekodik.css` — необязательные стандартные стили.

## Manifestator

Python-пакет `manifestator` создаёт manifest выпуска: транскрибирует
аудио локальной моделью через `transcribe.cpp`, чистит и суммаризирует текст
через Codex.

Установка Python-зависимостей:

```bash
uv sync
```

Передайте MP3 или другой аудиофайл. После каждого этапа Manifestator просит
подтверждение, а итоговый файл сохраняет в `output/<имя файла>/manifest.json`:

```bash
uv run manifestator podcast.mp3
```

Для многорольевой записи передайте каталог. Каждый аудиофайл верхнего уровня
станет отдельной ролью. TeamSpeak-дорожки синхронизируются по timestamp в имени,
остальные считаются начавшимися одновременно:

```bash
uv run manifestator ./tracks/
```

При первом запуске GigaAM скачивается в `~/.cache/timekodik/`. Готовые
результаты переиспользуются; `--force` пересобирает их заново.

Для ролевых TeamSpeak-дорожек, обрезки начала, другой модели и артефактов
остаётся расширенный конфиг:

```bash
cp episode.example.toml episode.toml
uv run manifestator --config episode.toml
```

Этап транскрибации сначала выделяет речь на каждой ролевой дорожке, создаёт фрагменты не длиннее 24 секунд и держит модель транскрибации загруженной на протяжении всего прогона. Результаты сохраняются в `build/` и могут быть проверены до вызова Codex.

Следующий этап исправляет через `gpt-5.6-luna` ошибки распознавания и речевой мусор, сохраняя исходный транскрипт. Таймкоды и итоговый manifest используют очищенную версию. Результат не записывается, если Codex потерял или переставил сегменты либо данные не прошли Pydantic/JSON Schema validation.

После каждого успешного шага создаётся отчёт в `build/debug/`. Manifestator inspector открывается сочетанием `Alt+Shift+D` или `Ctrl+Shift+D`; этап очистки показывает все изменённые сегменты в формате «до / после».

Последний этап создаёт переносимый каталог из `output_dir`:

```text
output/example/
├── manifest.json
└── artifacts/
    └── terminal-session.cast
```

Промежуточные аудио и JSON сохраняются автоматически в `build/`; задавать пути
к ним в конфиге не нужно.

URL файлов артефактов в manifest разрешаются относительно самого `manifest.json`; содержимое `.cast` в JSON или JavaScript не встраивается.

Перед публикацией JSON и `.cast` на другом origin нужно разрешить CORS для сайта
и отдавать корректные `Content-Type` (`application/json` и `application/x-asciicast`).
