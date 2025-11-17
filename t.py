import datetime
import time
import winsound
import threading
import pyttsx3

hora_alarme = "16:46"
parar = False

engine = pyttsx3.init()

def falar(texto):
    engine.say(texto)
    engine.runAndWait()

def esperar_parar():
    global parar
    input("\nPressione ENTER para desligar o alarme quando quiser...\n")
    parar = True


# --- Teste inicial ---
print("🔊 Teste inicial do alarme...")
winsound.Beep(3000, 1000)  # toque único
print("✔ Som OK!\n")

# Thread para capturar ENTER
threading.Thread(target=esperar_parar, daemon=True).start()

print("⏳ Aguardando horário:", hora_alarme)

while True:
    agora = datetime.datetime.now().strftime("%H:%M")

    if agora == hora_alarme:
        print("\n⏰ Alarme disparou! Tocando + falando...\n")
        while not parar:
            winsound.Beep(3000, 800)  # toque único
            falar("time sheet")
            time.sleep(0.5)

        print("🔕 Alarme desligado!")
        break

    if parar:
        print("❌ Alarme cancelado antes do horário.")
        break

    time.sleep(1)
