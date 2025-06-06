# Doom Benchmark

<p align="center">
  <img src="images/Logo.png" alt="Doom Logo" width="300"/>
</p>

<p align="center">
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/Python-3.13-blue.svg" alt="Python Version"/></a>
  <a href="https://opensource.org/licenses/Apache-2.0"><img src="https://img.shields.io/badge/License-Apache%202.0-green.svg" alt="License"/></a>
  <a href="https://huggingface.co/spaces/Vikhrmodels/DOoM-lb"><img src="https://img.shields.io/badge/🤗-HuggingFace%20Space-yellow.svg" alt="HuggingFace Space"/></a>
</p>

Doom - бенчмарк для оценки качества языковых моделей на математических и физических задачах на русском языке.

## 📖 О проекте

Doom - это инструмент для тестирования и оценки способности языковых моделей (LLM) решать задачи по математике и физике. Бенчмарк позволяет:

- Измерять точность решения математических задач
- Оценивать понимание физических концепций и способность решать задачи по физике
- Сравнивать производительность разных моделей на русскоязычном контенте
- Оценивать улучшения в способностях моделей к решению научных задач

Основная часть кодовой базы адаптирована из проекта OpenAI simpleeval.

## 📊 Поддерживаемые датасеты

1. **RussianMath** - разнообразные задачи по математике на русском языке (основной математический датасет)
2. **RussianPhysics** - задачи по физике на русском языке (основной физический датасет)

## 🚀 Запуск

### Установка зависимостей

Рекомендуется использовать `uv` и Python 3.13 для лучшей совместимости и производительности:

```bash
# Установка uv (если еще не установлен)
# Windows (PowerShell)
pip install uv

# Создание виртуального окружения
uv venv venv -p 3.13

# Активация окружения
# Windows
.venv\Scripts\activate
# Linux/macOS
# source .venv/bin/activate

# Установка зависимостей с помощью uv
uv pip install -r requirements.txt
```

Альтернативный вариант (стандартный pip):
```bash
pip install -r requirements.txt
```

### Базовый запуск (все датасеты)

```bash
python runner.py
```

### Выбор конкретного датасета

```bash
python runner.py --dataset russianmath  # Только датасет RussianMath
python runner.py --dataset physics      # Только датасет RussianPhysics
```

### Другие параметры

```bash
python runner.py --no-cache       # Игнорировать кэш и повторно выполнить оценку
python runner.py --max-workers 8  # Установить количество параллельных обработчиков
python runner.py --config path/to/config.yaml  # Указать альтернативный конфиг
```

### Справка по параметрам

```bash
python runner.py --help
```

## ⚙️ Конфигурация

Настройка выполняется через файлы YAML в директории `configs/`:

```yaml
configs/run.yaml  # Основной конфигурационный файл
```

Пример конфигурационного файла:

```yaml
model_list:
  - gpt-4o
  - claude-3-opus-20240229

# Общие настройки (применяются ко всем моделям, если не переопределены)
# num_examples: 100 # Опционально: Ограничить количество примеров для каждого датасета (по умолчанию используются все)
# debug: false # Опционально: Включить режим отладки

gpt-4o:
  model_name: gpt-4o
  endpoints:
    - api_base: "https://api.openai.com/v1"
      api_key: "your-api-key"
  api_type: openai
  parallel: 1
  system_prompt: "Вы - полезный помощник по математике и физике. Ответьте на русском языке."
  max_tokens: 32000
  # num_examples: 50 # Опционально: Переопределить количество примеров для этой модели

claude-3-opus-20240229:
  # ... конфигурация для Claude ...
```

**Описание параметров конфигурации:**

*   `model_list`: Список моделей для оценки.
*   `[model_name]`: Блок конфигурации для конкретной модели. Имя блока должно совпадать с именем в `model_list`.
    *   `model_name`: Имя модели (может отличаться от ключа блока, например, для локальных моделей).
    *   `endpoints`: Список эндпоинтов API.
        *   `api_base` / `base_url`: URL API.
        *   `api_key` / `credentials`: Ключ API или учетные данные (зависит от `api_type`).
    *   `api_type`: Тип API (`openai`, `gigachat` и т.д.).
    *   `parallel`: Количество параллельных запросов к API для этой модели.
    *   `system_prompt`: Системный промпт для модели.
    *   `max_tokens`: Максимальное количество токенов в ответе.
    *   `num_examples` (Опционально): Количество примеров для оценки этой модели. Переопределяет глобальное значение `num_examples`. **По умолчанию используются все доступные примеры из датасета.**
*   `num_examples` (Глобально, Опционально): Количество примеров для оценки для всех моделей, если не переопределено в блоке модели. **По умолчанию используются все доступные примеры из датасета.**
*   `debug` (Глобально, Опционально): Включить режим отладки для вывода дополнительной информации.

## 📝 Результаты тестирования

После запуска оценки автоматически будет сгенерирована таблица лидеров.
Она сохраняется в файле `results/leaderboard.md`.

Детальные результаты по каждой модели доступны в директории `results/details/`.

### Публикация результатов

Вы можете опубликовать результаты тестирования своей модели в общем лидерборде:

1. Клонируйте репозиторий и запустите тесты вашей модели
2. Загрузите результаты через [HuggingFace Space](https://huggingface.co/spaces/Vikhrmodels/DOoM-lb)
3. Дождитесь проверки и добавления результатов в лидерборд

Формат результатов для публикации в JSON формате:
```json
{
  "score": 0.586,
  "math_score": 0.8,
  "physics_score": 0.373,
  "total_tokens": 1394299,
  "evaluation_time": 4533.2,
  "system_prompt": "Вы - полезный помощник по математике и физике. Ответьте на русском языке."
}
```

## 🧪 Тестирование собственной модели

Чтобы протестировать собственную модель на бенчмарке Doom:

1. Разверните свою модель локально или через API
2. Добавьте конфигурацию вашей модели в `configs/run.yaml`
3. Запустите бенчмарк с помощью `python runner.py`

Подробные инструкции по хостингу моделей через VLLM и их тестированию на бенчмарке доступны в файле [Instruction.md](Instruction.md).

## 📚 Структура проекта

- `/configs` - конфигурационные файлы
- `/src` - исходный код бенчмарка
- `/results` - результаты тестирования
  - `/results/details` - подробные результаты по каждой модели
  - `/results/cache` - кэш результатов для ускорения повторных запусков
- `/images` - графические ресурсы проекта

## 🤗 Лидерборд

Текущий лидерборд с результатами тестирования различных моделей доступен на [HuggingFace Space](https://huggingface.co/spaces/Vikhrmodels/DOoM-lb).

## 📄 Лицензия

Проект распространяется под лицензией Apache 2.0. См. файл LICENSE для получения дополнительной информации.