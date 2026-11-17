# Untuk mengambil session cookie dari X & thread
# session cookie yang akan di gunakan oleh plugin headless_browser
# session cookie akan di masukan ke file .env :
#   TWITTER_AUTH_COOKIE=?????
#   THREADS_SESSION_ID=?????
#
# pasang dulu packet pythonnya:
#   pip install playwright
#   playwright install chromium
#

import asyncio
import os
import re
from playwright.async_api import async_playwright

# Tentukan path absolut ke .env agar aman dieksekusi dari mana saja
ENV_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env')

def update_env_file(key: str, value: str):
    """Membaca file .env, mengupdate key jika ada, atau menambahkannya jika belum."""
    if not os.path.exists(ENV_PATH):
        with open(ENV_PATH, 'w') as f:
            f.write(f"{key}={value}\n")
        print(f"✅ File .env dibuat dan {key} berhasil disimpan.")
        return

    with open(ENV_PATH, 'r') as f:
        lines = f.readlines()

    key_found = False
    with open(ENV_PATH, 'w') as f:
        for line in lines:
            if line.startswith(f"{key}="):
                f.write(f"{key}={value}\n")
                key_found = True
            else:
                f.write(line)

        if not key_found:
            f.write(f"{key}={value}\n")

    print(f"✅ {key} berhasil diperbarui di dalam .env!")

async def capture_twitter_cookie():
    print("\n🌐 Membuka browser untuk X (Twitter)...")
    print("👉 Silakan login secara manual. Skrip akan menunggu dan menangkap cookie otomatis.")

    async with async_playwright() as p:
        # headless=False agar jendela browser terlihat oleh manusia
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()

        await page.goto("https://twitter.com/login")

        auth_token = None
        # Looping untuk mengecek cookie setiap 2 detik sampai auth_token ditemukan
        while not auth_token:
            await asyncio.sleep(2)
            cookies = await context.cookies()
            for cookie in cookies:
                if cookie['name'] == 'auth_token':
                    auth_token = cookie['value']
                    break

        print("\n🔓 Login X berhasil terdeteksi!")
        update_env_file("TWITTER_AUTH_COOKIE", auth_token)
        await browser.close()

async def capture_threads_cookie():
    print("\n🌐 Membuka browser untuk Threads (via Instagram)...")
    print("👉 Silakan login secara manual. Skrip akan menunggu dan menangkap cookie otomatis.")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()

        # Threads menggunakan sistem autentikasi Instagram
        await page.goto("https://www.threads.net/login")

        session_id = None
        while not session_id:
            await asyncio.sleep(2)
            cookies = await context.cookies()
            for cookie in cookies:
                if cookie['name'] == 'sessionid':
                    session_id = cookie['value']
                    break

        print("\n🔓 Login Threads berhasil terdeteksi!")
        update_env_file("THREADS_SESSION_ID", session_id)
        await browser.close()

async def main():
    print("========================================")
    print("🤖 DEVI-SUBHADRA: SESSION COOKIE GRABBER")
    print("========================================")
    print("Pilih platform yang ingin di-login:")
    print("1. X (Twitter)")
    print("2. Threads")
    print("3. Keduanya")

    pilihan = input("Masukkan angka (1/2/3): ")

    if pilihan == '1':
        await capture_twitter_cookie()
    elif pilihan == '2':
        await capture_threads_cookie()
    elif pilihan == '3':
        await capture_twitter_cookie()
        await capture_threads_cookie()
    else:
        print("Pilihan tidak valid.")

if __name__ == "__main__":
    asyncio.run(main())
