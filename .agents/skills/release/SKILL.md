---
name: release
description: >-
  Мержит текущую ветку в main, создаёт и пушит semver-тег через make release, ждёт
  публикации timekodik на npm и появления GitHub Release. Триггеры: /release,
  "релизни", "выкати релиз", "мерж бамп релиз".
argument-hint: "[patch|minor|major]"
---

# Релиз timekodik

Версия проекта определяется git-тегом `vMAJOR.MINOR.PATCH`. Версию в
`package.json` вручную не менять: workflow получает её из тега перед публикацией.

## 1. Bump-уровень

- Аргумент: `patch`, `minor` или `major`.
- Если аргумент не указан и уровень не очевиден, спросить пользователя.

## 2. Предусловия

- Проверить `git status --porcelain`. При незакоммиченных изменениях остановиться;
  самостоятельно их не коммитить.
- Проверить наличие remote `origin` и workflow `.github/workflows/release.yml`.

## 3. Мерж в main

- Если текущая ветка не `main`, выполнить `git checkout main` и
  `git merge --ff-only <branch>`. При невозможности fast-forward остановиться.
- Выполнить `git push origin main` до создания тега.

## 4. Тег

- Выполнить `make release BUMP=<bump>`.
- Получить версию командой `git describe --tags --abbrev=0`.

## 5. Ожидание публикации

До 20 минут ждать GitHub Release для полученной версии, проверяя workflow
`release.yml`. Если workflow завершился с ошибкой, показать failed logs и
остановиться.

```bash
ready=""
for _ in $(seq 1 60); do
  if gh release view "$VERSION" >/dev/null 2>&1; then ready=1; break; fi
  failed=$(gh run list --workflow=release.yml -L 10 \
    --json databaseId,headBranch,status,conclusion \
    --jq ".[] | select(.headBranch==\"$VERSION\" and .status==\"completed\" and .conclusion!=\"success\") | .databaseId")
  if [ -n "$failed" ]; then
    gh run view "$failed" --log-failed
    exit 1
  fi
  sleep 20
done
```

Если `ready` остался пустым, сообщить о таймауте и дать ссылку на workflow. Успех
не объявлять.

При первом релизе package ещё не существует, поэтому trusted publisher нельзя
создать заранее. Для него используется временный GitHub secret `NPM_TOKEN`.
После первой публикации настроить trusted publisher:

```bash
npm trust github timekodik \
  --file release.yml \
  --repo grigory51/timekodik \
  --allow-publish
```

После успешной настройки удалить `NPM_TOKEN`: workflow продолжит публиковать через
OIDC.

## 6. Итог

Сообщить версию, URL GitHub Release, npm package и CDN:
`https://cdn.jsdelivr.net/npm/timekodik@VERSION/dist/addon/timekodik.js`.
