# MACRO KEYPAD - poznámky a postup testování

## 1. Co firmware dělá

- 12 kláves (matice 4x3) -> vyšle `KEY:<znak>` a `MACRO:<akce>` podle aktivního profilu.
- 2 enkodéry (EC11) -> vyšle `ENC1:<pozice>` / `ENC2:<pozice>`.
- Stisk enkodéru 1 -> `ENC1_SW:1` (PC app: mute masteru).
- Stisk enkodéru 2 -> `ENC2_SW:1` + `MACRO:custom` (PC app: mute cílové aplikace).
- Profil se přepíná jen příkazem z PC (`P:<n>`) nebo v GUI, ne tlačítkem enkodéru.
- Přijímá telemetrii z PC a zobrazuje ji na displeji.
- Vše je neblokující (`millis()`), enkodéry mají kvadraturní dekodér a debounce.

## 2. Sériový protokol (115200 baud)

### ESP32 -> PC
```
KEY:<znak>            napr. KEY:1
MACRO:<akce>          napr. MACRO:undo
ENC1:<pozice>         napr. ENC1:12
ENC2:<pozice>         napr. ENC2:-3
ENC1_SW:1             stisk tlacitka enkoderu 1
ENC2_SW:1             stisk tlacitka enkoderu 2
PROFILE:<n>:<nazev>   napr. PROFILE:2:MEDIA
```

### PC -> ESP32 (ukončeno `\n`)
```
T:CPU=45;GPU=30;TEMP=52
P:2
N:2:MEDIA
H:hello
```
`T:` aktualizuje telemetrii, `P:` přepne profil (1..3),
`N:<n>:<nazev>` přejmenuje stránku zobrazenou na displeji (posílá PC app
při připojení a po každé změně bindings) — firmware odpoví `PAGENAME:<n>:<nazev>`
a `PROFILE:<n>:<nazev>`,
`H:<text>` zobrazí testovací text na displeji (5 s, pak se vrátí UI) —
pro rychlou kontrolu vykreslení/orientace displeje.

### Význam akcí `MACRO:<akce>` (patří k PC host app)
| Akce | Co s tím udělá PC host |
|------|------------------------|
| `app:<název>` | spustí aplikaci na PC (např. `app:spotify` -> Spotify) |
| ostatní (`undo`, `cut`, ...) | zatím jen log (mapování dodělá host app) |

`ENC1:<n>` posílá **absolutní pozici** enkodéru — host app počítá delta
a mění hlasitost PC. `ENC1_SW:1` = přepnutí módu host app.

## 3. Mapování kláves

Klávesy:
```
1 2 3
4 5 6
7 8 9
* 0 #
```

### Profil 1 - EDIT
| Key | Akce | Key | Akce | Key | Akce |
|-----|------|-----|------|-----|------|
| 1 | app:spotify | 2 | redo | 3 | save |
| 4 | copy | 5 | cut | 6 | paste |
| 7 | find | 8 | replace | 9 | select_all |
| * | close | 0 | delete | # | enter |

### Profil 2 - MEDIA
| Key | Akce | Key | Akce | Key | Akce |
|-----|------|-----|------|-----|------|
| 1 | play | 2 | stop | 3 | next |
| 4 | prev | 5 | vol_up | 6 | vol_down |
| 7 | mute | 8 | rewind | 9 | forward |
| * | fullscreen | 0 | subtitles | # | snapshot |

### Profil 3 - CODE
| Key | Akce | Key | Akce | Key | Akce |
|-----|------|-----|------|-----|------|
| 1 | build | 2 | run | 3 | debug |
| 4 | step_over | 5 | step_in | 6 | step_out |
| 7 | breakpoint | 8 | continue | 9 | stop |
| * | terminal | 0 | format | # | commit |

## 4. Displej - přepínač panelu

V `src/main.cpp`:

```cpp
#define PANEL_76X284 1   // vždy 1: simulace i reál PANEL jsou teď 76x284 (284x76 landscape)
```

- Simulace používá **vlastní Wokwi čip** `chip-st7789` (viz sekce 6.1 níže),
  který emuluje ST7789 284x76 s centrováním 82/18 přesně tak, jak to dělá
  `Adafruit_ST7789::init(76, 284)` + `setRotation(1)`.
- Reálný panel je stejný: 2.25" ST7789 76x284.

## 5. Build

### VS Code (PlatformIO)
1. Otevři projekt.
2. PlatformIO: Build.
3. PlatformIO: Upload (jen pro reálnou desku).

### CLI
```powershell
pio run
pio run -t upload
```

Build výstupy jdou do `C:/Temp/keypad_build` (kvůli diakritice v cestě).
Aby je Wokwi našel "ve workspace", je v projektu junction `wokwi_build`
odkazující na `C:/Temp/keypad_build`. V `wokwi.toml` jsou proto relativní cesty:
```toml
firmware = 'wokwi_build/upesy_wroom/firmware.bin'
elf = 'wokwi_build/upesy_wroom/firmware.elf'
```
Pokud junction smažeš nebo přesuneš projekt, vytvoř ho znovu:
```powershell
cmd /c mklink /J "<projekt>\wokwi_build" "C:\Temp\keypad_build"
```

## 6. Test ve Wokwi (simulace)

1. Ujisti se, že firmware je nabuildovaný (`pio run`) a v projektu existuje
   junction `wokwi_build` -> `C:\Temp\keypad_build` (viz sekce 5).
2. Ujisti se, že v `diagram.json` jsou propojené sériové piny:
   ```json
   [ "esp:TX", "$serialMonitor:RX", "", [] ],
   [ "esp:RX", "$serialMonitor:TX", "", [] ]
   ```
   (už jsou tam)
3. Spusť simulaci:
   - VS Code: příkaz `Wokwi: Start Simulator` (použije `diagram.json` a `wokwi.toml`).
4. Otevři Serial Monitor / terminál na **115200 baud**.
5. Po startu musí přijít:
   ```
   === MACRO KEYPAD FIRMWARE ===
   ESP32 DevKit v1 | ST7789 | 4x3 Keypad | 2x Encoders
   --- READY ---
   Panel: 284x76
   PROFILE:1:EDIT
   ```
6. Otestuj klávesy (klikej na tlačítka v diagramu):
   - `1` -> `KEY:1` + `MACRO:app:spotify`
   - `5` -> `KEY:5` + `MACRO:cut`
   - `#` -> `KEY:#` + `MACRO:enter`
7. Otestuj enkodér 1 (otoč kolečkem):
   - `ENC1:1`, `ENC1:2`, ... (případně do mínusu)
8. Stiskni tlačítko enkodéru 1 (mute toggle — obsluhuje host app):
   - `ENC1_SW:1` (host app: toggle mute)
9. Stiskni tlačítko enkodéru 2 (placeholder pro vlastní účel):
   - `ENC2_SW:1` + `MACRO:custom`
10. Pošli telemetrii do simulace (vstup do Serial Monitoru):
    ```
    T:CPU=45;GPU=30;TEMP=52
    ```
    Na displeji se objeví `C45 G30 52C`.
11. Pošli přepnutí profilu:
    ```
    P:3
    ```
    -> `PROFILE:3:CODE`, na displeji `CODE`.
12. Otestuj vykreslení displeje textem:
    ```
    H:hello
    ```
    -> v terminálu `DISPLAY:hello`, na displeji velký žlutý text `hello`
    uprostřed se zeleným rámečkem — za 5 s se obnoví normální UI.

### 6.1 Vlastní displejový chip (chip-st7789)

Wokwi nemá oficiální 76x284 panel, proto projekt obsahuje vlastní simulátor
displeje postavený na Wokwi Chips API:

| Soubor                          | Obsah                                    |
|---------------------------------|------------------------------------------|
| `chips/st7789.chip.c`           | zdrojový kód chipu (SPI + framebuffer)   |
| `chips/st7789.chip.json`        | popis chipu: piny + `display` 284x76     |
| `chips/st7789.chip.wasm`        | zkompilovaný WASM binární obraz          |
| `chips/wokwi-api.h`             | oficiální Wokwi API hlavička             |

Charakteristika simulačního panelu:
- native RAM centrování: **colstart 82, rowstart 18** (centrované	okno uvnitř
  240x320 grid, shodné s `Adafruit_ST7789::init(76, 284)`),
- barvy RGB565, MADCTL (MX/MY/MV) plně podporován,
- piny: `VCC, GND, SCL, SDA, RES, DC, CS, BLK` (stejné jako reální modul),
- part v `diagram.json` je `chip-st7789` (`[[chip]]` v `wokwi.toml`).

Rebuild WASM po úpravě chipu:
```powershell
& "$env:TEMP\opencode\wokwi\wokwi-cli.exe" chip compile chips/st7789.chip.c -o chips/st7789.chip.wasm
```
(První spuštění si stáhne WASI-SDK do `~/.wokwi`, na další je to okamžité.)

Debug chipu: `printf()` ze `st7789.chip.c` se objeví v záložce
**Chips Console** pod diagramem (např. `chip-st7789: 284x76 framebuffer display ready`).

### 6.2 Orientace displeje
Firmware je pevně na `setRotation(1)`. Pokud by byl obraz v simulaci
zrcadlený/vzhůru nohama, změň v `src/main.cpp` v `initDisplay()` na
`tft.setRotation(3)` a přebilduj. (Chip podporuje MX/MY/MV, takže mezi
rotacemi 0..3 jen jedna bude vypadat upřímně pro tvoje U.)

### Když tlačítka nereagují v simulaci
Wokwi pushbutton má piny `1.l`, `1.r`, `2.l`, `2.r` (ne `1in`/`2in`).
V `diagram.json` musí být řádek na `1.l` a sloupec na `2.l`.

### Když enkodér skáče dvakrát
Nastav v `src/main.cpp`:
```cpp
#define ENC_STEPS_PER_DETENT 2
```

### Když se enkodér točí opačně
Prohoď `CLK` a `DT` v definicích `E1_*` / `E2_*` (nebo v `diagram.json`).

## 7. Test na reálném HW

1. Reálný panel je teď stejný jako simulace (76x284). Zjisti, že `PANEL_76X284 1`.
2. Zkontroluj zapojení podle tabulky v projektu (3.3 V, GND, SPI, matice, enkodéry).
3. Připoj ESP32 přes USB.
4. Nahraj firmware:
   ```powershell
   pio run -t upload
   ```
5. Otevři terminál:
   ```powershell
   pio device monitor -b 115200
   ```
6. Očekávané výstupy jsou stejné jako v simulaci.
7. Zkontroluj displej:
   - pokud je obraz posunutý/rozřezaný, zkus `tft.setRotation(3)` nebo uprav `PANEL_W`/`PANEL_H`.
   - podsvícení je na GPIO17 (`TFT_BL`), v kódu se nastavuje na HIGH.

## 8. Časté problémy

- **`Wokwi: diagram.json ... is not valid JSON`** = soubor měl UTF-8 **BOM**
  na začátku. `diagram.json`, `wokwi.toml` a všechny `chips/*.json` musí být
  bez BOM. Ošetřeno; kdyby se vrátilo, ulož soubor bez BOM.
- **Link error `cannot open map file ...`** = cesta s diakritikou. Řeší `build_dir` v `platformio.ini`.
- **`firmware.bin not found in workspace`** = `wokwi.toml` musí mít relativní cestu a firmware musí být dostupný přes junction `wokwi_build` (viz sekce 5).
- **`UnknownPackageError`** = špatný název knihovny v `lib_deps`; používej registry názvy (`chris--a/Keypad`).
- **Nic v terminálu** = chybí propojení `$serialMonitor` v `diagram.json` nebo špatný baud (115200).
- **Displej bílý/černý v simulaci** = čip se nepovedl: spusť simulaci, ve
  **Chips Console** musí být `chip-st7789: 284x76 framebuffer display ready`;
  pokud ne, rebuild WASM (sekce 6.1) a restart simulace.
- **Displej černý + malý barevný pruh** = chyba mapování v čipu (RASET přepisoval
  X-okno místo Y-okna). Opraveno: `set_case_window()`/`set_row_window()`;
  po úpravě chipu vždy rebuild WASM (sekce 6.1) a restart simulace.
- **Displej zrcadlený svisle (písmena „naopak", M jako W)** = chip apply-
  oval MY flip z MADCTL; virtuální sklo nemá fyzikální polaritu, flip se na
  něm neaplikuje (rotace 1: MADCTL 0xA0 = MY|MV). Opraveno v `put_pixel()`.
- **Displej posunutý/zrcadlený** = zkuste rotaci 1 vs 3 v `initDisplay()` (sekce 6.2).

## 9. PC host app (pc\KeypadHost.ps1)

PC aplikace, která čte výstup zařízení (COM port, 115200) a vykonává akce na PC:

| Vstup z ESP32 | Akce host app |
|---------------|---------------|
| MACRO:app:<název> | spustí aplikaci přes URI scheme (spotify:, discord:, ...) |
| ENC1:<n> | delta pozice -> hlasitost PC (keybd_event VK_VOLUME_UP/DOWN, max 5 kroků) |
| ENC1_SW:1 | toggle mute systému (VK_VOLUME_MUTE) |
| ENC2: / ENC2_SW:1 | custom (účel se určí později; zatím log) |
| ostatní MACRO: | zatím jen log |

Spuštění:
```powershell
powershell -ExecutionPolicy Bypass -File pc\KeypadHost.ps1 -Port COM5 -Baud 115200
powershell -ExecutionPolicy Bypass -File pc\KeypadHost.ps1 -Port COM5 -DryRun   # jen loguje, nespouští
```

Poznámka: ve Wokwi simulace host app zafunguje až s reálným zařízením
(virtuální COM ve Wokwi není dostupný). Firmware zatím jen posílá
protokolový řádek `MACRO:app:spotify` na tlačítko 1 (profil EDIT) —
viz sekce 6 krok 6.

### 9.1 Sim režim (reálné akce na PC bez HW)

Wokwi serial nelze napojit na COM port, ale host app může vykonávat
**tytéž řádky protokolu**, které sim vypisuje:

```powershell
powershell -ExecutionPolicy Bypass -File pc\KeypadHost.ps1 -Sim
```
→ interaktivně zadej řádky (`MACRO:app:spotify`, `ENC1:3`, `ENC1_SW:1`, ...)
→ Spotify se otevře, hlasitost se změní, mute přepne.

Přehrání celé sekvence (soubor `pc\test-actions.txt`):
```powershell
powershell -ExecutionPolicy Bypass -File pc\KeypadHost.ps1 -Sim -ReplayFile pc\test-actions.txt
```
Sekvence: spustí Spotify, hlasitost +2 kroky, -2 kroky, mute on, mute off
(síťový efekt na hlasitost = nula). `+ `-DryRun` jen loguje bez akcí.

## 10. PC aplikace v2 (pc\KeypadApp) - Python + PySide6

Nová GUI aplikace, která nahrazuje PS1 skript pro reálné používání (PS1 zůstává
jako sim/replay nástroj). Python 3.14 + PySide6 + pyserial + pycaw (audio).

### 10.1 Instalace a spuštění
```powershell
python -m pip install -r pc\KeypadApp\requirements.txt
python pc\KeypadApp\main.py                    # auto-detekce portu + auto-connect
python pc\KeypadApp\main.py --port COM5        # vynuti konkretni port
python pc\KeypadApp\main.py --port COM5 --dry-run   # jen loguje akce
```
Připojení: aplikace nejdřív zkusí poslední použitý port (`last_port`
v `bindings.json`), pak auto-detekci podle VID/PID USB-serial čipu
(CP210x, CH340, Espressif native USB, FTDI).

### 10.2 Architektura
| Soubor | Obsah |
|--------|-------|
| `main.py` | vstup, QApplication, tray ikona |
| `window.py` | hlavní okno: connect bar, 3 profily, 4x3 grid editor, log |
| `sim_panel.py` | simulace 12 tlačítek + 2 enkodérů (bez HW) |
| `serial_link.py` | pyserial vlákno, auto-reconnect, tx/rx, auto-detekce portu |
| `protocol.py` | parsování řádků protokolu + buildery příkazů |
| `config.py` | load/save `bindings.json`, defaulty, summary |
| `actions.py` | akční engine (ctypes user32): app launch/focus, hotkey, media, text |
| `audio.py` | Windows Core Audio (pycaw): master + per-app hlasitost/mute |
| `bindings.json` | konfigurace bindings (vytvoří se automaticky) |

Princip: **firmware posílá eventy (`KEY:`, `ENC:`), PC aplikace vlastní bindingy**
a vykonává akce. Změna bindingu = jen úprava v GUI, bez přeflashování.
Při připojení app pošle `N:<n>:<nazev>` pro všechny profily, takže displej
ukazuje jména stránek z `bindings.json`.

### 10.3 Binding model (bindings.json)
```json
{
  "version": 1,
  "baud": 115200,
  "last_port": "COM5",
  "audio": { "volume_step": 2, "target_app": "Spotify.exe" },
  "profiles": [
    {
      "name": "EDIT",
      "keys": {
        "1": { "type": "app", "target": "spotify:", "mode": "auto" },
        "3": { "type": "hotkey", "combo": "Ctrl+S" }
      }
    }
  ]
}
```
| Typ | Pole | Chování |
|-----|------|---------|
| `app` | `target` (URI scheme nebo cesta k .exe), `mode` = `auto`/`always`/`focus`, `process` (nepovinné) | `auto`: běží -> focus, neběží -> start; `always`: vždy start; `focus`: jen focus |
| `hotkey` | `combo` např. `Ctrl+Shift+S` | stisk zkratky do aktivního okna (SendInput/keybd_event) |
| `media` | `code` = `play_pause`, `stop`, `next`, `prev`, `vol_up`, `vol_down`, `mute` | multimediální VK kódy |
| `text` | `text` | otypuje string (unicode) |
| `page` | `page` 1..3 | přepne stránku (`P:<n>`) |
| `none` | - | nic |

Focus hledá okno podle názvu procesu (`EnumWindows` + `QueryFullProcessImageName`).
Pro URI scheme se proces odhaduje (`spotify:` -> `Spotify.exe`) nebo lze
nastavit explicitně polem `process`.

### 10.4 Testování bez tlačítek (sim panel)
Vpravo je panel **Simulace**: klikací 4x3 klávesy a `-`/`+`/`SW` pro oba
enkodéry. Eventy jdou přes stejný handler jako reálný serial, takže bindings
lze testovat i bez fyzických tlačítek. Reálný displej + enkodéry fungují
současně (přes COM port).

### 10.5 Omezení
- Wokwi nemá virtuální COM port -> simulace firmware + PC app nejde propojit
  přímo; použij sim panel (10.4) nebo PS1 `-Sim`.
- `MACRO:` řádky app jen loguje (vykonává se z `KEY:` podle bindings);
  PS1 naopak vykonává `MACRO:` - nespouštěj oba hosty na stejném portu.

### 10.6 Audio mixer (pycaw)
`audio.py` ovládá Windows Core Audio přímo (žádné simulované stisky
hlasitostních kláves):

| Vstup | Akce |
|-------|------|
| `ENC1:<pos>` | master hlasitost default zařízení, krok `audio.volume_step` % (výchozí 2) |
| `ENC1_SW:1` | toggle mute masteru |
| `ENC2:<pos>` | hlasitost zvukové session aplikace `audio.target_app` (např. `Spotify.exe`) |
| `ENC2_SW:1` | toggle mute téže aplikace |

- Cílová aplikace se vybírá v horní liště (combo „Audio app“).
- Absolutní pozice enkodéru se převádí na deltu v `MainWindow.handle_encoder`.
- Procenta jsou shodná s Windows volume mixerem (`IAudioEndpointVolume`,
  `ISimpleAudioVolume`). Aplikace musí mít aktivní audio session, jinak
  log hlásí „has no audio session“.
- `python -c "import audio; print(audio.status_text())"` = rychlý test bez GUI.

### 10.7 Auto-detekce portu
`serial_link.port_candidates()` skóruje porty podle VID/PID
(`10C4` CP210x, `1A86` CH340, `303A` Espressif, `0403` FTDI) a popisu;
Bluetooth/ACPI porty se ignorují. `detect_port()` vrací nejlepší kandidát.
Aplikace při startu zkusí `last_port`, potom detekci, a když nic nenajde,
jen zaloguje dostupné porty.
