import requests

WB_TOKEN = "YOUR_WB_TOKEN_HERE"

headers = {"Authorization": WB_TOKEN}
url_coeffs = "https://common-api.wildberries.ru/api/tariffs/v1/acceptance/coefficients"

resp = requests.get(url_coeffs, headers=headers)

print(f"Статус: {resp.status_code}")
if resp.status_code == 429:
    # Ищем специфичные заголовки WB
    retry_in = resp.headers.get("X-Ratelimit-Retry")
    reset_in = resp.headers.get("X-Ratelimit-Reset")
    remaining = resp.headers.get("X-Ratelimit-Remaining")
    
    print("\n--- Ответ сервера (Заголовки) ---")
    print(f"X-Ratelimit-Retry (через сколько повторить): {retry_in} сек.")
    print(f"X-Ratelimit-Reset (когда обнулится лимит): {reset_in}")
    print(f"X-Ratelimit-Remaining (осталось запросов): {remaining}")
    
    if retry_in:
        print(f"\n⚠️ Ждать осталось: {int(retry_in) // 60} мин. {int(retry_in) % 60} сек.")
    else:
        print("\nЗаголовок X-Ratelimit-Retry не найден. Выведем все заголовки для проверки:")
        for k, v in resp.headers.items():
            print(f"{k}: {v}")
elif resp.status_code == 200:
    print("✅ Лимиты сбросились! Можно запускать основного бота.")
else:
    print(f"❌ Неизвестная ошибка: {resp.text}")