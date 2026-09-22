# wacom-panscroll

Pan-scroll para un botón del lápiz Wacom en **Wayland/GNOME** — lo que
`xsetwacom ... Button N "pan"` te daba en X11, y que ni libinput ni mutter
implementan de forma nativa (todavía).

🇬🇧 [Read in English](README.md)

Mantené presionado un botón lateral del lápiz y arrastrá → scroll suave,
sensible a la velocidad, en vez de mover el cursor. Soltá el botón → vuelve
a la normalidad.

## Por qué existe esto

En X11, el driver `xf86-input-wacom` te dejaba hacer:

```bash
xsetwacom --set "Wacom Intuos S Pen stylus" Button 3 "pan"
```

y un botón lateral del lápiz se convertía en un disparador de pan/scroll. En
Wayland no hay equivalente:

- [gnome-control-center#1645](https://gitlab.gnome.org/GNOME/gnome-control-center/-/issues/1645)
  y [gtk#5570](https://gitlab.gnome.org/GNOME/gtk/-/issues/5570) llevan años
  abiertos como pedidos de funcionalidad, sin implementación.
- El protocolo Wayland [`tablet-v2`](https://wayland.app/protocols/tablet-v2)
  directamente no tiene concepto de "pan" para botones del lápiz — deja el
  mapeo de acciones enteramente en manos del compositor, y mutter no
  implementa ninguno.
- `libinput` 1.30+ agregó un sistema de plugins en Lua que en teoría podría
  hacer esto, pero (a) el compositor tiene que habilitar explícitamente la
  carga de plugins, y mutter no lo hace, y (b) incluso un parche de prueba
  reportado en el
  [foro de GNOME Discourse](https://discourse.gnome.org/t/scrolling-emulation-with-wacom-tablets-stylus-in-wayland/33611)
  quedó en punto muerto: los eventos de scroll aparecían en herramientas de
  debug pero nunca llegaban al compositor como scroll real.

Así que esto existe como una solución alternativa en espacio de usuario: un
pequeño daemon que hace a nivel `evdev`/`uinput` lo que `xf86-input-wacom`
hacía a nivel de driver de X11 — independiente del compositor, funciona hoy.

## Cómo funciona

Versión corta: el daemon agarra de forma exclusiva el dispositivo real del
lápiz (para ser el único que lo lee), y reemite los eventos a través de dos
dispositivos virtuales (`uinput`):

1. **Un clon de la tableta** — mismas capacidades que el lápiz real (presión,
   inclinación, posición absoluta...), así que dibujar/mover el cursor no se
   ve afectado. Mientras el botón configurado está presionado, su movimiento
   deja de reenviarse por acá.
2. **Un pequeño dispositivo "puntero"**, usado solo para el scroll. Resultó
   necesario por dos razones nada obvias (encontradas a prueba y error — ver
   los comentarios al inicio de [`wacom_panscroll.py`](wacom_panscroll.py)
   para la historia completa):
   - Los eventos de rueda enviados desde un dispositivo etiquetado como
     tableta son descartados silenciosamente por el camino de código
     "tablet-tool" de libinput. Un dispositivo que parece un puntero
     absoluto normal (sin eje de presión) no tiene ese problema.
   - Ese segundo dispositivo tiene su propia posición de puntero para
     mutter, así que hay que sincronizarlo con la posición actual del lápiz
     justo cuando arranca un gesto de pan — si no, el scroll cae donde haya
     quedado el puntero la última vez, no donde está visualmente el cursor.

Mientras el botón disparador está presionado, el movimiento del lápiz se
convierte en scroll suave y sensible a la velocidad (`REL_WHEEL_HI_RES`, lo
mismo que usan los touchpads) en vez de mover el cursor — arrastres lentos
scrollean poco, rápidos scrollean mucho, con una curva de aceleración encima
para que no se sienta lineal/robótico. Esto coincide con el comportamiento
del driver moderno (2022+) `xf86-input-wacom` de panscroll suave, verificado
contra su código fuente.

**Limitación cosmética conocida:** ese segundo dispositivo puntero muestra
brevemente su propio ícono de cursor tipo mouse superpuesto al cursor de la
tableta mientras estás haciendo pan activamente. Wayland no tiene una opción
de "ocultar este cursor" por dispositivo, así que esto no se puede evitar
del todo desde un daemon de entrada en espacio de usuario — ver los
comentarios del código si querés profundizar.

## Requisitos

- Linux con sesión Wayland (probado en GNOME; el enfoque es agnóstico del
  compositor a nivel de entrada, pero toda la investigación de "qué botón
  hace qué" y las pruebas se hicieron en GNOME/mutter)
- Python 3
- El módulo de Python [`evdev`](https://python-evdev.readthedocs.io/)
- Una tableta Wacom cuyo lápiz reporte `BTN_STYLUS`/`BTN_STYLUS2` y
  `ABS_PRESSURE` (básicamente cualquier tableta Wacom con lápiz)
- Acceso de lectura a `/dev/input/event*` (grupo `input`) y de escritura a
  `/dev/uinput` — `install.sh` los revisa y te ayuda a configurarlos

## Instalación

```bash
git clone https://github.com/<vos>/wacom-panscroll.git
cd wacom-panscroll
./install.sh
```

El script:
1. Revisa que exista `python3` y el módulo `evdev` (te da el comando de
   instalación correcto para tu distro si falta).
2. Detecta automáticamente tu dispositivo de lápiz (te pregunta cuál elegir
   si encuentra más de uno).
3. Revisa/arregla la pertenencia al grupo para `/dev/input/*` y el acceso de
   escritura a `/dev/uinput` (puede pedir `sudo` para estos dos pasos — te
   explica por qué antes de hacer nada).
4. Genera e instala una unidad `systemd --user`, la habilita y la arranca.

Podés volver a correr `./install.sh` cuando quieras — es idempotente.

### Instalación manual

Si preferís no correr el script: mirá
[`wacom-panscroll.service.example`](wacom-panscroll.service.example) para la
plantilla de la unidad y qué editar.

## Configuración

Todo el ajuste se hace con variables de entorno, puestas con líneas
`Environment=` en la unidad de systemd (`systemctl --user edit --full
wacom-panscroll.service`, o editando directamente
`~/.config/systemd/user/wacom-panscroll.service`), y después:

```bash
systemctl --user daemon-reload
systemctl --user restart wacom-panscroll.service
```

| Variable | Default | Significado |
|---|---|---|
| `WACOM_PANSCROLL_DEVICE` | `Wacom Intuos S Pen` | Nombre exacto del dispositivo a agarrar (`install.sh` lo configura por vos) |
| `WACOM_PANSCROLL_BUTTON` | `stylus2` | Qué botón lateral dispara el pan: `stylus` (inferior) o `stylus2` (superior) |
| `WACOM_PANSCROLL_SENSITIVITY` | `300` | Unidades de movimiento de la tableta por "click" de scroll. Más bajo = más rápido/sensible |
| `WACOM_PANSCROLL_ACCEL` | `1.6` | Exponente de la curva de aceleración por velocidad. `1.0` = lineal, sin aceleración |
| `WACOM_PANSCROLL_ACCEL_MAX` | `6.0` | Tope del multiplicador de aceleración, para que un movimiento muy rápido no te lance a través de todo un documento |
| `WACOM_PANSCROLL_INVERT_Y` | `0` | `1` para invertir la dirección del scroll vertical |
| `WACOM_PANSCROLL_HSCROLL` | `1` | `0` para desactivar el scroll horizontal (solo vertical) |

## Verificar que funciona

```bash
systemctl --user status wacom-panscroll.service
journalctl --user -u wacom-panscroll.service -f
```

Deberías ver que encuentra tu dispositivo y crea dos virtuales. Después:
dibujá normal (no debería sentirse distinto), mantené presionado el botón
configurado y arrastrá el lápiz (debería scrollear en vez de mover el
cursor), soltá (vuelve a la normalidad).

## Desinstalar

```bash
./uninstall.sh
```

Elimina el servicio. Tu tableta vuelve al comportamiento por defecto de
Wayland que tenía antes. (No revierte la pertenencia al grupo ni la regla
udev de `/dev/uinput` de la instalación — son inofensivas de dejar, pero
podés borrar `/etc/udev/rules.d/99-wacom-panscroll-uinput.rules` vos mismo
con `sudo` si también querés sacarlas.)

## Alternativas consideradas

Si esto no encaja con tu configuración, hay otros caminos, con las
ventajas/desventajas discutidas en el código/historial de commits de este
proyecto:

- **[OpenTabletDriver](https://opentabletdriver.net/)** — un reemplazo
  completo y multiplataforma del driver de tableta. Mucho más pesado (su
  propio daemon, GUI, base de datos de dispositivos) si lo único que querés
  es pan-scroll, pero vale la pena si querés reemplazar toda tu pila de
  manejo de tableta.
- **[input-remapper](https://github.com/sezanzeb/input-remapper)** — bueno
  para remapeo discreto de botón→tecla, pero no está pensado para gestos
  continuos de movimiento→scroll como este.
- **Esperar soporte nativo** — seguí los issues de GNOME linkeados arriba;
  si mutter/libinput algún día lo implementan de forma nativa, desactivá
  este daemon (`./uninstall.sh`) y usá eso en su lugar.

## Licencia

MIT — ver [LICENSE](LICENSE).
