# timekodik

Прикрутить таймкоды к `<audio>` на существующей странице.

Standalone TypeScript-библиотека добавляет к нативному аудиоплееру кликабельные
главы, транскрипт и синхронные артефакты, не заменяя сам плеер.

## Установка

Опубликуйте manifest выпуска, подключите стили и script:

```html
<link
  rel="stylesheet"
  href="https://cdn.jsdelivr.net/npm/@grigory51/timekodik@latest/dist/addon/timekodik.css"
>
<script
  defer
  src="https://cdn.jsdelivr.net/npm/@grigory51/timekodik@latest/dist/addon/timekodik.js"
  data-audio="#audio-player"
  data-manifest="https://cdn.example/episodes/example/manifest.json"
></script>
```

`@latest` автоматически выбирает последнюю опубликованную версию. jsDelivr
кеширует такой alias до семи дней; для production можно зафиксировать major
(`@1`) или точную версию (`@1.2.3`).

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
- `.pa-gallery`, `.pa-gallery img`, `.pa-gallery figcaption` — галерея;
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

Renderer `gallery` показывает одно изображение без элементов управления, а для
нескольких изображений добавляет листалку. В manifest изображения задаются так:

```json
{
  "type": "gallery",
  "source": {
    "url": "photos/1.jpg",
    "urls": ["photos/1.jpg", "photos/2.jpg"]
  }
}
```

`url` дублирует первое изображение для совместимости manifest с timekodik 0.1.0.

## Сборка frontend

```bash
npm install
npm run check
```

Результат:

- `dist/addon/timekodik.js` — standalone IIFE;
- `dist/addon/timekodik.css` — необязательные стандартные стили.

## Manifestator

Python-пакет `manifestator` принимает исходное аудио и файлы артефактов, а на
выходе создаёт готовый каталог с `manifest.json`. GigaAM и Whisper независимо
распознают речь локально, Codex выбирает итоговый текст и выделяет главы.

### Быстрый запуск

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

При первом запуске GigaAM скачивается в `~/.cache/timekodik/`, а Whisper
`large-v3-turbo` — в стандартный cache Hugging Face. На Apple Silicon Whisper
работает через MLX/Metal, на остальных платформах — через CTranslate2. Готовые
результаты следующих запусков переиспользуются; `--force` пересобирает их заново.

Если выпуск после записи монтировался, передайте финальный MP3:

```bash
uv run manifestator ./tracks/ --final-audio ./podcast-final.mp3
```

Вместо локального пути можно передать `https://` URL. Файл один раз сохранится в
`build/final-audio/`.

Для ролевых TeamSpeak-дорожек, обрезки начала, другой модели и артефактов
остаётся расширенный конфиг:

```bash
cp episode.example.toml episode.toml
uv run manifestator --config episode.toml
```

По умолчанию для очистки текста и глав используется Luna. Модель можно заменить
в конфиге; её смена автоматически инвалидирует соответствующие checkpoints:

```toml
codex_model = "gpt-5.6-luna"
# codex_service_tier = "fast"
```

`codex_service_tier = "fast"` ускоряет вызовы Codex, но стоит вдвое дороже.

### Как устроен pipeline

Основная команда не задаёт вопросов и подходит для локального запуска и CI:

```bash
uv run manifestator --config episode.toml
```

Порядок запуска читается сверху вниз в `manifestator/cli.py`. Реализация каждого
этапа находится в `manifestator/stages.py`; работа с аудио, ASR, review и
alignment вынесена в одноимённые специализированные модули.

1. **Проверка входных данных.** Проверяется наличие `ffmpeg`, `ffprobe`, Codex,
   модели транскрибации, дорожек и финального master. Файлы артефактов
   проверяются при сборке manifest.
2. **Сведение.** Для нескольких дорожек timestamps из имён TeamSpeak задают их
   смещение относительно первой записи. Дорожки проходят high-pass filter и
   compressor, смешиваются в mono, обрезаются по `content_start_seconds` и
   нормализуются через двухпроходный `loudnorm`. Один готовый аудиофайл не
   пересводится.
3. **Транскрибация.** `ffmpeg` выделяет участки речи и режет их на фрагменты не
   длиннее 24 секунд. GigaAM и Whisper независимо распознают одинаковые
   фрагменты. GigaAM задаёт сегменты и timestamps, Whisper сохраняет вторую
   гипотезу с границами отдельных слов. У ролевых дорожек сохраняется speaker.
4. **Проверка текста.** Codex обрабатывает по 100 сегментов, видит обе
   ASR-гипотезы, соседний контекст и размеченный словарь. Он выбирает итоговый
   текст, исправляет явные ошибки STT и убирает речевой мусор, но не может
   объединять, удалять или переставлять сегменты. Каждая пачка кешируется по
   hash всех входных данных.
5. **Выравнивание по финальному master.** Этот этап выполняется только при
   наличии `final_audio`. Он переносит ролевой транскрипт и артефакты на шкалу
   опубликованного монтажа; подробнее механизм описан ниже.
6. **Главы.** Codex получает уже итоговый транскрипт, выделяет крупные темы и
   выбирает границы только из существующих timestamps сегментов.
7. **Сборка.** Создаётся `manifest.json`, а файлы артефактов копируются в
   `output_dir/artifacts/`. Все URL внутри manifest относительны самому
   `manifest.json`.

### Как работает выравнивание финального монтажа

Исходное ролевое аудио содержит правильные speaker labels, но после монтажа его
временная шкала становится неверной: редактор может вырезать паузы и реплики,
добавить заставку или изменить длительность выпуска. Финальный MP3, напротив,
содержит правильное время, но не содержит информацию о ролях. Manifestator
связывает эти два представления в два прохода.

Сначала финальный MP3 повторно транскрибируется GigaAM и Whisper без
diarization, затем результат проверяет Codex. Токены очищенного ролевого
и финального транскриптов сопоставляются через
`SequenceMatcher`. Так определяются сохранившиеся реплики, вырезанные сегменты и
приблизительные пары `sourceSeconds → finalSeconds`. Если сопоставилось меньше
60% ролевых сегментов, pipeline останавливается вместо создания недостоверного
manifest.

Текстовые timestamps имеют погрешность до размера STT-фрагмента, поэтому каждый
предварительный anchor уточняется по звуку:

- оба файла временно декодируются в mono PCM 800 Hz с полосой 80–350 Hz;
- вокруг source anchor берётся восьмисекундный акустический образец;
- во финальном master он ищется в окне ±35 секунд от текстовой оценки;
- normalized cross-correlation вычисляется через FFT;
- совпадения с confidence ниже `0.5` отбрасываются.

Если акустически подтвердилось меньше половины текстовых anchors, pipeline также
останавливается. Оставшиеся anchors записываются в
`build/<episode>.alignment.json`. Для каждого таймкода применяется offset
ближайшего подтверждённого anchor. Текст и timestamps берутся только из
финального master, а ролевые дорожки назначают ему speaker. Вырезанные монтажом
реплики не возвращаются; начало и конец каждого артефакта переносятся
независимо. Финальный MP3 становится единственным master clock для виджета.

### Временная шкала конфига

`content_start_seconds` задаётся на шкале исходной записи и определяет, сколько
секунд отрезается перед началом source audio. Все `start_seconds` и
`end_seconds` артефактов задаются уже относительно этого обрезанного source
audio, а не относительно финального MP3.

Например, если содержательная запись начинается на `99.196`, то после
`content_start_seconds = 99.196` её начало имеет время `0`. Артефакт, который
начинается через 60 секунд после этой точки, должен получить
`start_seconds = 60`. При наличии `final_audio` Manifestator сам перенесёт эти
60 секунд через alignment; вручную учитывать заставки и монтажные вырезки не
надо.

В TOML финальный master задаётся локальным путём или URL:

```toml
content_start_seconds = 99.196
final_audio = "https://cdn.example/podcast-final.mp3"

[[artifacts]]
id = "terminal-demo"
type = "asciinema"
title = "Демонстрация в терминале"
start_seconds = 60.0
end_seconds = 90.0
local_source = "data/terminal.cast"
```

### Промежуточные результаты и проверка

Основные результаты сохраняются в `build/`:

- `<episode>.mp3` — сведённый source audio;
- `<episode>.transcript.<gigaam-model>.json` — локальный STT по ролям;
- `<episode>.transcript.<whisper-model>.json` — слова второй ASR-гипотезы;
- `<episode>.transcript.clean.json` — версия после очистки Codex;
- `<episode>.transcript.final.<gigaam-model>.json` — GigaAM STT финального master;
- `<episode>.transcript.final.<whisper-model>.json` — Whisper STT финального master;
- `<episode>.transcript.final.clean.json` — итоговый текст финального master;
- `<episode>.glossary.json` — внутреннее состояние кандидатов словаря;
- `<episode>.alignment.json` — подтверждённые acoustic anchors и удалённые
  сегменты;
- `<episode>.transcript.aligned.json` — ролевой транскрипт на финальной шкале;
- `<episode>.chapters.json` — темы и таймкоды.

После каждого успешного шага создаётся отчёт в `build/debug/`. Manifestator
inspector открывается сочетанием `Alt+Shift+D` или `Ctrl+Shift+D`; этап очистки
показывает изменённые сегменты в формате «до / после», а этап alignment — старые
и новые timestamps и список удалённых сегментов.

### Словарь терминов

При расхождении ASR-гипотез Codex собирает кандидатов. Они размечаются отдельной
командой с тем же исходным каталогом или конфигом:

```bash
uv run manifestator-glossary --config episode.toml
# или
uv run manifestator-glossary ./recording
```

В полноэкранном редакторе стрелки выбирают термин, `Enter` переводит фокус в
поле правильного написания. «Подтвердить» и «Игнорировать» сразу сохраняют
решение. Рядом показываются версии GigaAM, Whisper и контекст.

Подтверждённые соответствия накапливаются локально в игнорируемом Git файле
`glossary.json` в корне проекта. Они передаются Codex как словарь и Whisper как
`hotwords` в следующих выпусках.
После разметки повторно запустите `manifestator`: изменившиеся термины
инвалидируют только затронутые пачки очистки, поэтому `--force` не нужен.

Последний этап создаёт переносимый каталог из `output_dir`:

```text
output/example/
├── manifest.json
└── artifacts/
    ├── terminal-session.cast
    ├── photos-1.jpg
    └── photos-2.jpg
```

Содержимое `.cast`, изображения и другие ресурсы не встраиваются в JSON или
JavaScript. Они остаются отдельными файлами рядом с manifest.

Перед публикацией JSON и `.cast` на другом origin нужно разрешить CORS для сайта
и отдавать корректные `Content-Type` (`application/json` и `application/x-asciicast`).
