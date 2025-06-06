import yaml
from typing import List, Dict, Tuple, Union, Any
import openai
import time
import re
import threading
import logging
import traceback
import json
from json.decoder import JSONDecodeError

from .types import SamplerBase
from gigachat import GigaChat
from gigachat.models import Chat, Messages

# Настройка логирования только в файл, без вывода в консоль
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.FileHandler("api_requests.log", mode="a")],
)
logger = logging.getLogger("sampler")

# Увеличиваем максимальное количество попыток и задержку между ними
API_MAX_RETRY = 17
API_RETRY_SLEEP = 7
API_ERROR_OUTPUT = "Error during API call. Please try again."

# Расширенный список шаблонов сообщений об ошибках
API_ERROR_PATTERNS = [
    # Стандартное сообщение об ошибке
    r"###\s*Model\s*Response\s*Error\s*during\s*API\s*call",
    # Часто встречающиеся сообщения об ошибках
    r"Error\s*during\s*API\s*call.*try\s*again",
    r"API\s*(call|request)\s*(failed|error|timeout)",
    r"Exception\s*occurred.*API",
    r"(failed|error|unable)\s*to\s*(generate|get|fetch)\s*response",
    # Ошибка отсутствия ответа
    r"The\s*model\s*did\s*not\s*provide\s*a\s*(response|answer)",
    # Если ответ содержит только технические сообщения или метаданные API
    r"^(Error:|Warning:|Exception:|API Error:)",
]


# Параметры повтора для ошибок JSON
JSON_ERROR_MAX_RETRY = 12  # Максимальное количество повторов при ошибках JSON
JSON_ERROR_RETRY_DELAY = 5  # Начальная задержка между повторами (в секундах)


# Глобальный счетчик времени для контроля интервалов между запросами
class RateLimiter:
    """
    Класс для ограничения частоты запросов к API.
    
    Используется для соблюдения ограничений API и предотвращения лимитов скорости.
    """
    
    def __init__(self, delay: float = 0.0):
        """
        Инициализирует ограничитель скорости запросов.
        
        Args:
            delay: Минимальная задержка между запросами в секундах
        """
        self.delay = delay
        self.last_request_time = 0
        self.lock = threading.Lock()

    def wait_if_needed(self) -> None:
        """
        Ожидает, если необходимо, перед следующим запросом для соблюдения заданной задержки.
        
        Если с момента последнего запроса прошло меньше времени, чем указано в delay,
        функция блокируется до достижения нужной задержки.
        """
        if self.delay <= 0:
            return

        with self.lock:
            current_time = time.time()
            elapsed = current_time - self.last_request_time

            # Если прошло меньше времени, чем задержка, ждем
            if elapsed < self.delay:
                wait_time = self.delay - elapsed
                if wait_time > 0.1:  # Не логируем очень короткие задержки
                    logger.debug(f"Waiting {wait_time:.2f}s before next API call")
                time.sleep(wait_time)

            # Обновляем время последнего запроса
            self.last_request_time = time.time()


def safe_response_dump(response: Any) -> str:
    """
    Безопасно сериализует объект ответа API в строку для логирования.
    
    Обрабатывает различные типы ответов, включая None, 
    и предотвращает ошибки сериализации.
    
    Args:
        response: Любой объект ответа API, который нужно сериализовать
        
    Returns:
        Строковое представление объекта для логирования
    """
    if response is None:
        return "None"

    try:
        # Если у объекта есть метод to_dict или __dict__
        if hasattr(response, "to_dict") and callable(getattr(response, "to_dict")):
            response_dict = response.to_dict()
            return json.dumps(response_dict, ensure_ascii=False, indent=2, default=str)
        elif hasattr(response, "__dict__"):
            response_dict = response.__dict__
            # Фильтруем внутренние атрибуты, начинающиеся с '_'
            filtered_dict = {k: v for k, v in response_dict.items() if not k.startswith('_')}
            return json.dumps(filtered_dict, ensure_ascii=False, indent=2, default=str)
        # Если это словарь или другой тип, который можно сериализовать
        elif isinstance(response, (dict, list, str, int, float, bool)):
            return json.dumps(response, ensure_ascii=False, indent=2, default=str)
        else:
            # Для всех остальных типов преобразуем в строку
            return f"{type(response).__name__}: {str(response)}"
    except Exception as e:
        # Если произошла ошибка при сериализации, возвращаем информацию о типе объекта и ошибке
        return f"[Error serializing {type(response).__name__}: {str(e)}]"


class OaiSampler(SamplerBase):
    """
    Класс для взаимодействия с различными API языковых моделей.
    
    Поддерживает OpenAI, GigaChat и другие API, совместимые с форматом OpenAI.
    Обеспечивает надежную обработку запросов с повторными попытками при ошибках
    и контролем ограничений скорости.
    """
    
    # Создаем словарь ограничителей скорости для разных моделей API
    _rate_limiters = {}
    _rate_limiters_lock = threading.Lock()

    @classmethod
    def get_rate_limiter(cls, api_type: str, model_name: str, delay: float) -> RateLimiter:
        """
        Получает ограничитель скорости для конкретного API и модели.
        
        Args:
            api_type: Тип API (openai, gigachat и др.)
            model_name: Название модели
            delay: Задержка между запросами
            
        Returns:
            Экземпляр RateLimiter для указанной комбинации API и модели
        """
        key = f"{api_type}_{model_name}"
        with cls._rate_limiters_lock:
            if key not in cls._rate_limiters:
                cls._rate_limiters[key] = RateLimiter(delay)
            return cls._rate_limiters[key]

    def __init__(self, config_path: str):
        """
        Инициализирует объект для работы с API языковой модели.
        
        Args:
            config_path: Путь к файлу конфигурации YAML
            
        Raises:
            ValueError: Если в конфигурации отсутствуют необходимые параметры аутентификации
        """
        # Загружаем конфиг
        with open(config_path, "r") as f:
            self.config = yaml.safe_load(f)

        # Получаем параметры для выбранной модели
        model_name = self.config["model_list"][0]  # Берем первую модель из списка
        self.model_config = self.config.get(model_name, {})

        # Определяем тип API
        self.api_type = self.model_config.get("api_type", "openai")

        # Получаем параметры из endpoints
        if "endpoints" in self.model_config:
            endpoint = self.model_config["endpoints"][0]

            # Унифицированное получение API ключа или credentials
            self.api_key = endpoint.get("api_key", self.config.get("api_key"))
            self.credentials = endpoint.get("credentials")

            # Унифицированное получение base_url
            self.base_url = endpoint.get("api_base", endpoint.get("base_url"))

            # Дополнительные параметры для GigaChat
            self.scope = endpoint.get("scope", "GIGACHAT_API_CORP")
            self.profanity_check = endpoint.get("profanity_check", True)
            self.timeout = endpoint.get("timeout", 60.0)
        else:
            self.api_key = self.config.get("api_key")
            self.credentials = None
            self.base_url = None
            self.scope = "GIGACHAT_API_CORP"
            self.profanity_check = True
            self.timeout = 60.0

        # Проверка наличия необходимых учетных данных
        if self.api_type == "openai" and not self.api_key:
            raise ValueError(f"API key not found in config for model {model_name}")
        elif self.api_type == "gigachat" and not self.credentials:
            raise ValueError(f"Credentials not found in config for model {model_name}")

        # Инициализируем клиент OpenAI если нужно
        self.client = None
        if self.api_type == "openai":
            if self.base_url:
                self.client = openai.OpenAI(
                    api_key=self.api_key, base_url=self.base_url
                )
            else:
                self.client = openai.OpenAI(api_key=self.api_key)

        self.model_name = self.model_config.get("model_name", model_name)
        self.temperature = self.config.get("temperature", 0.0)

        # Получаем max_tokens из настроек конкретной модели, если он там есть
        # Иначе используем общее значение из конфига или значение по умолчанию
        self.max_tokens = self.model_config.get(
            "max_tokens", self.config.get("max_tokens", 2048)
        )

        self.system_prompt = self.model_config.get("system_prompt", None)
        self.debug = self.config.get("debug", False)

        # Получаем задержку между запросами для модели или используем общее значение
        self.request_delay = self.model_config.get(
            "request_delay", self.config.get("request_delay", 0.0)
        )

        # Инициализируем ограничитель скорости для этой модели
        self.rate_limiter = self.get_rate_limiter(
            self.api_type, self.model_name, self.request_delay
        )

        if self.debug:
            logger.debug(f"Initialized OaiSampler for {self.model_name}")
            logger.debug(f"API Type: {self.api_type}")
            logger.debug(f"Base URL: {self.base_url}")
            logger.debug(f"Request delay: {self.request_delay} sec")
            if self.api_key:
                logger.debug(f"API Key: {self.api_key[:8]}...")
            elif self.credentials:
                logger.debug(f"Using credentials for {self.api_type}")

    def _pack_message(self, content: str, role: str = "user") -> Dict[str, str]:
        """
        Упаковывает сообщение в формат для API.
        
        Args:
            content: Текст сообщения
            role: Роль сообщения (system, user, assistant)
            
        Returns:
            Словарь, представляющий сообщение в формате API
        """
        return {"role": role, "content": content}

    def contains_error_patterns(self, text: str) -> bool:
        """
        Проверяет наличие шаблонов ошибок в тексте.
        
        Args:
            text: Текст для проверки
            
        Returns:
            True если текст содержит шаблоны ошибок или пустой, иначе False
        """
        if not text:
            return True  # Пустой ответ - тоже ошибка

        for pattern in API_ERROR_PATTERNS:
            if re.search(pattern, text, re.IGNORECASE):
                return True
        return False

    def chat_completion_gigachat(
        self,
        model: str,
        messages: List[Dict[str, str]],
        temperature: float,
        max_tokens: int,
    ) -> Tuple[str, Dict[str, int]]:
        """
        Обработка запроса к GigaChat API с улучшенным механизмом повторных попыток.

        Args:
            model: Название используемой модели GigaChat
            messages: Список сообщений для контекста
            temperature: Параметр температуры для генерации (случайность)
            max_tokens: Максимальное количество токенов в ответе

        Returns:
            Кортеж (текст_ответа, метаданные)
        """
        # Создаем api_dict для GigaChat из унифицированных параметров
        api_dict = {
            "credentials": self.credentials,
            "base_url": self.base_url,
            "scope": self.scope,
            "profanity_check": self.profanity_check,
            "timeout": self.timeout,
        }

        output: str = API_ERROR_OUTPUT
        metadata: Dict[str, int] = {"total_tokens": 0}

        # Записываем в лог краткую информацию о запросе
        logger.info(f"API request: [{model}] (GigaChat)")

        # Создаем клиент и настраиваем параметры только один раз перед циклом
        client = GigaChat(model=model, verify_ssl_certs=False, **api_dict)

        # Настраиваем параметры для GigaChat
        top_p: float = 1
        if temperature == 0:
            temperature = 1
            top_p = 0

        # Преобразуем сообщения в формат GigaChat
        giga_messages = [Messages.parse_obj(m) for m in messages]
        chat = Chat(
            messages=giga_messages,
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p,
        )

        # Максимальное количество повторных попыток
        for attempt in range(API_MAX_RETRY):
            # Прогрессивное увеличение времени между попытками
            if attempt > 0:
                retry_delay: float = API_RETRY_SLEEP * (
                    1 + attempt * 0.5
                )  # Увеличиваем задержку с каждой попыткой
                logger.info(
                    f"Model [{model}]: Retry #{attempt + 1}/{API_MAX_RETRY}, delay: {retry_delay:.1f}s"
                )
                time.sleep(retry_delay)

            try:
                response = client.chat(chat)
                output = response.choices[0].message.content

                # Проверяем содержимое ответа на наличие шаблонов ошибок
                if self.contains_error_patterns(output):
                    error_msg = output.strip()  # Удаляем пробелы по краям
                    log_content = error_msg[:100] + "..." if len(error_msg) > 100 else error_msg
                    logger.warning(
                        f"Model [{model}] (attempt {attempt + 1}): Error pattern in response"
                    )
                    # Логируем полный ответ при обнаружении ошибки
                    logger.warning(f"Full response: {safe_response_dump(response)}")
                    
                    if attempt < API_MAX_RETRY - 1:
                        continue  # Повторяем запрос
                    else:  # Если это последняя попытка и ответ содержит ошибку
                        output = f"API returned error pattern: {log_content}"  # Обновляем вывод для возврата
                        break  # Прерываем цикл после последней попытки

                # Извлекаем информацию о токенах
                if hasattr(response, "usage") and response.usage:
                    metadata["prompt_tokens"] = getattr(
                        response.usage, "prompt_tokens", 0
                    )
                    metadata["completion_tokens"] = getattr(
                        response.usage, "completion_tokens", 0
                    )
                    metadata["total_tokens"] = getattr(
                        response.usage, "total_tokens", 0
                    )

                # Записываем в лог только краткую информацию о успешном запросе
                logger.info(
                    f"Model [{model}]: Success, tokens: {metadata['total_tokens']}"
                )

                # Успешно получен ответ без ошибок в содержимом
                break

            except Exception as e:
                # При ошибке логируем только ключевую информацию и полный JSON ответа
                logger.error(f"Model [{model}] (attempt {attempt + 1}): {type(e).__name__}: {str(e)}")
                
                # Логируем полный ответ API при ошибке
                error_json = {
                    "error_type": type(e).__name__,
                    "error_message": str(e),
                    "request_model": model,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                    "attempt": attempt + 1,
                    "total_attempts": API_MAX_RETRY
                }
                logger.error(f"Error details: {json.dumps(error_json, ensure_ascii=False)}")

                # Если это последняя попытка, фиксируем ошибку
                if attempt == API_MAX_RETRY - 1:
                    logger.error(f"Model [{model}]: All {API_MAX_RETRY} retry attempts exhausted")
                    output = f"Error during API call: {str(e)}"

        return output, metadata

    def __call__(
        self, messages: List[Dict[str, str]], return_metadata: bool = False
    ) -> Union[str, Tuple[str, Dict[str, int]]]:
        """
        Отправляет запрос к API и возвращает ответ.

        Args:
            messages: Список сообщений для диалога с моделью
            return_metadata: Флаг для возврата метаданных (токены, задержки)

        Returns:
            При return_metadata=False: строка с ответом модели
            При return_metadata=True: кортеж (ответ, метаданные)

        Raises:
            Exception: В случае ошибок при обращении к API
        """
        # Ждем, если нужно соблюдать ограничение скорости запросов
        self.rate_limiter.wait_if_needed()

        if self.debug:
            msg_preview = (
                messages[0]["content"][:50] + "..."
                if messages and len(messages[0]["content"]) > 50
                else ""
            )
            logger.debug(
                f"Sending request to {self.model_name}, first message: {msg_preview}"
            )

        # Добавляем system prompt если он есть
        if self.system_prompt:
            messages = [
                self._pack_message(content=self.system_prompt, role="system")
            ] + messages

        # Для OpenAI API добавляем специальную обработку с повторами для JSONDecodeError
        if self.api_type != "gigachat":
            for json_retry in range(JSON_ERROR_MAX_RETRY):
                try:
                    return self._process_openai_request(messages, return_metadata)
                except Exception as e:
                    # Проверяем, является ли ошибка JSONDecodeError или TypeError (для обработки некорректной структуры ответа)
                    is_retryable_error = (
                        isinstance(e, JSONDecodeError)
                        or "JSONDecodeError" in str(e)
                        or "Expecting value" in str(e)
                        or isinstance(e, TypeError)
                    )

                    if is_retryable_error:
                        retry_delay = JSON_ERROR_RETRY_DELAY * (1 + json_retry * 0.5)
                        logger.warning(
                            f"Model [{self.model_name}] ({type(e).__name__} attempt {json_retry + 1}/{JSON_ERROR_MAX_RETRY}): retrying in {retry_delay:.1f}s. Error: {str(e)}"
                        )
                        time.sleep(retry_delay)
                        # Если это не последняя попытка, продолжаем цикл
                        if json_retry < JSON_ERROR_MAX_RETRY - 1:
                            continue

                    # Для всех других ошибок или если исчерпали попытки для JSONDecodeError/TypeError
                    error_msg = f"API call error: {self.model_name}, {self.api_type}"
                    if self.api_key:
                        error_msg += f" (API key: {self.api_key[:4]}...)"
                    elif self.credentials:
                        error_msg += f" (using credentials for {self.api_type})"
                    error_msg += f" - {type(e).__name__}: {str(e)}"

                    logger.error(error_msg)
                    raise Exception(
                        f"API call failed for model {self.model_name}. Check logs for details."
                    ) from e
        else:
            # Обработка для GigaChat остается без изменений
            result, metadata = self.chat_completion_gigachat(
                model=self.model_name,
                messages=messages,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
            )

            if return_metadata:
                return result, metadata
            return result

    def _process_openai_request(
        self, messages: List[Dict[str, str]], return_metadata: bool = False
    ):
        """
        Обрабатывает запрос к OpenAI API и возвращает результат.
        Выделено в отдельный метод для более удобного механизма повторов.
        """
        # Prepare arguments for the API call
        api_args = {
            "model": self.model_name,
            "messages": messages,
            "temperature": self.temperature,
        }
        # Add max_tokens only if it has a value
        if self.max_tokens is not None:
            api_args["max_tokens"] = self.max_tokens

        # Записываем только краткую информацию о запросе для OpenAI
        logger.info(f"API request: [{self.model_name}]")

        try:
            response = self.client.chat.completions.create(**api_args)
        except Exception as e:
            # Подробно логируем только ошибки API
            logger.error(f"Model [{self.model_name}]: API error: {type(e).__name__}: {str(e)}")
            
            # Логируем полный ответ API при ошибке
            error_json = {
                "error_type": type(e).__name__,
                "error_message": str(e),
                "request_model": self.model_name,
                "request_temperature": self.temperature,
                "request_max_tokens": self.max_tokens,
            }
            logger.error(f"Error details: {json.dumps(error_json, ensure_ascii=False)}")
            
            raise e

        # Инициализируем метаданные
        metadata: Dict[str, int] = {"total_tokens": 0}

        # Извлекаем информацию о токенах из разных типов ответов
        if hasattr(response, "usage") and response.usage:
            metadata["prompt_tokens"] = getattr(response.usage, "prompt_tokens", 0)
            metadata["completion_tokens"] = getattr(
                response.usage, "completion_tokens", 0
            )
            metadata["total_tokens"] = getattr(response.usage, "total_tokens", 0)
        elif isinstance(response, dict) and "usage" in response:
            metadata["prompt_tokens"] = response["usage"].get("prompt_tokens", 0)
            metadata["completion_tokens"] = response["usage"].get(
                "completion_tokens", 0
            )
            metadata["total_tokens"] = response["usage"].get("total_tokens", 0)

        try:
            result: str = ""

            # Стандартный путь для OpenAI API
            # Добавляем проверку на None перед доступом к choices
            if hasattr(response, "choices") and response.choices is not None and len(response.choices) > 0:
                if hasattr(response.choices[0], "message") and hasattr(
                    response.choices[0].message, "content"
                ):
                    result = response.choices[0].message.content

                    # Проверяем содержимое ответа на наличие шаблонов ошибок
                    if self.contains_error_patterns(result):
                        error_msg = result.strip()
                        log_content = (
                            error_msg[:100] + "..."
                            if len(error_msg) > 100
                            else error_msg
                        )
                        logger.warning(
                            f"Model [{self.model_name}]: Error pattern in response: {log_content}"
                        )
                        # Логируем полный ответ при обнаружении ошибки
                        logger.warning(f"Full response: {safe_response_dump(response)}")
                    else:
                        # Для успешных запросов - только модель, статус и токены
                        logger.info(f"Model [{self.model_name}]: Success, tokens: {metadata['total_tokens']}")

                    if return_metadata:
                        return result, metadata
                    return result
            # Добавляем проверку на None для словарного формата
            elif isinstance(response, dict) and "choices" in response and response["choices"] is not None:
                if len(response["choices"]) > 0:
                    if (
                        "message" in response["choices"][0]
                        and "content" in response["choices"][0]["message"]
                    ):
                        result = response["choices"][0]["message"]["content"]

                        # Проверяем содержимое ответа на наличие шаблонов ошибок
                        if self.contains_error_patterns(result):
                            error_msg = result.strip()
                            log_content = (
                                error_msg[:100] + "..."
                                if len(error_msg) > 100
                                else error_msg
                            )
                            logger.warning(
                                f"Model [{self.model_name}]: Error pattern in response: {log_content}"
                            )
                            # Логируем полный ответ при обнаружении ошибки
                            logger.warning(f"Full response: {safe_response_dump(response)}")
                        else:
                            # Для успешных запросов - только модель, статус и токены
                            logger.info(f"Model [{self.model_name}]: Success, tokens: {metadata['total_tokens']}")

                        if return_metadata:
                            return result, metadata
                        return result

            # Если ничего не нашли, но есть response в строковом виде
            if isinstance(response, str):
                if return_metadata:
                    return response, metadata
                return response

            # Последняя попытка получить ответ
            if hasattr(response, "content"):
                if return_metadata:
                    return response.content, metadata
                return response.content

            # Если все методы не сработали, возвращаем строку с ошибкой формата
            error_msg = (
                f"Failed to extract response content. Response type: {type(response)}"
            )
            logger.warning(
                f"Model [{self.model_name}]: {error_msg}. Response dump: {safe_response_dump(response)}"
            )
            # Генерируем ошибку, чтобы вызвать повторную попытку в __call__
            # Используем TypeError, так как он уже обрабатывается в __call__ для повторов
            # или можно создать кастомный класс ошибки
            raise TypeError(f"Unexpected response structure: {error_msg}")


        except Exception as content_error:
            # Логируем ошибку извлечения контента
            logger.error(
                f"Model [{self.model_name}]: Error extracting content from response: {str(content_error)}"
            )
            logger.error(
                f"Model [{self.model_name}]: Traceback: {traceback.format_exc()}"
            )
            logger.error(
                f"Model [{self.model_name}]: Response dump: {safe_response_dump(response)}"
            )

            # Перебрасываем исключение, чтобы его мог поймать __call__ для повторной попытки
            raise content_error
