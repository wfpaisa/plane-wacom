# wacom-panscroll

Pan-scroll para un botón del lápiz Wacom en **Wayland/GNOME** — lo que
`xsetwacom ... Button N "pan"` te daba en X11, y que ni libinput ni mutter
implementan de forma nativa (todavía).

🇬🇧 [Read in English](README.md)

## Instalar y ejecutar

```bash
git clone https://github.com/<vos>/wacom-panscroll.git
cd wacom-panscroll
./install.sh
```

`install.sh` detecta tu lápiz automáticamente, revisa/arregla los permisos
que necesita (grupo `input`, `/dev/uinput` — puede pedir `sudo`), e
instala+arranca un servicio `systemd --user`. Se puede volver a correr
cuando quieras, es idempotente.

Mantené presionado el botón lateral configurado y arrastrá el lápiz → scroll
suave en vez de mover el cursor. Soltá → vuelve a la normalidad.

```bash
systemctl --user status wacom-panscroll.service    # ver que esté corriendo
journalctl --user -u wacom-panscroll.service -f     # ver logs en vivo
./uninstall.sh                                       # desinstalar
```

**Instalación manual** (sin el script): copiá
[`wacom-panscroll.service.example`](wacom-panscroll.service.example) a
`~/.config/systemd/user/wacom-panscroll.service`, editá las rutas, y después
`systemctl --user daemon-reload && systemctl --user enable --now wacom-panscroll.service`.

### Requisitos

Sesión Wayland (probado en GNOME/mutter) · Python 3 +
[`evdev`](https://python-evdev.readthedocs.io/) · una tableta Wacom cuyo
lápiz reporte `BTN_STYLUS`/`BTN_STYLUS2` + `ABS_PRESSURE` · acceso de
lectura a `/dev/input/event*` y de escritura a `/dev/uinput` (`install.sh`
revisa ambos).

## Configuración

Se ajusta con líneas `Environment=` en la unidad de systemd
(`~/.config/systemd/user/wacom-panscroll.service`), y después:

```bash
systemctl --user daemon-reload && systemctl --user restart wacom-panscroll.service
```

| Variable | Default | Significado |
|---|---|---|
| `WACOM_PANSCROLL_DEVICE` | `Wacom Intuos S Pen` | Nombre exacto del dispositivo a agarrar (`install.sh` lo configura por vos) |
| `WACOM_PANSCROLL_BUTTON` | `stylus2` | Botón disparador: `stylus` (inferior) o `stylus2` (superior) |
| `WACOM_PANSCROLL_SENSITIVITY` | `320` | Unidades de movimiento de la tableta por "click" de scroll. Más bajo = más rápido |
| `WACOM_PANSCROLL_ACCEL` | `1.6` | Exponente de la aceleración por velocidad. `1.0` = lineal, sin aceleración |
| `WACOM_PANSCROLL_ACCEL_MAX` | `4.0` | Tope del multiplicador de aceleración |
| `WACOM_PANSCROLL_INVERT_Y` | `0` | `1` para invertir el scroll vertical |
| `WACOM_PANSCROLL_HSCROLL` | `1` | `0` para desactivar el scroll horizontal (solo vertical) |
| `WACOM_PANSCROLL_EDGE_SYNC` | `1` | `0` para desactivar la sincronización del cursor cerca de los bordes (ver abajo) |
| `WACOM_PANSCROLL_EDGE_ZONE` | `0.05` | Fracción del rango de la tableta, desde cada borde, considerada "cerca del borde" |

## Cómo funciona

Esto existe porque Wayland no tiene equivalente al mapeo de botón-pan de
X11: [gnome-control-center#1645](https://gitlab.gnome.org/GNOME/gnome-control-center/-/issues/1645)
y [gtk#5570](https://gitlab.gnome.org/GNOME/gtk/-/issues/5570) son pedidos
de funcionalidad abiertos hace años, el protocolo
[`tablet-v2`](https://wayland.app/protocols/tablet-v2) no tiene concepto de
"pan" para botones del lápiz, y un prototipo de plugin Lua de `libinput`
[quedó en punto muerto](https://discourse.gnome.org/t/scrolling-emulation-with-wacom-tablets-stylus-in-wayland/33611).

Así que este daemon lo hace en espacio de usuario, igual que
`xf86-input-wacom` lo hacía en el driver de X11: agarra el lápiz de forma
exclusiva y reemite los eventos a través de dos dispositivos virtuales
(`uinput`) — un clon de la tableta (dibujo/cursor sin cambios) y un pequeño
puntero tipo "mouse" usado solo para el scroll, porque un dispositivo
etiquetado como tableta tiene sus eventos de rueda descartados
silenciosamente por el camino de código "tablet-tool" de libinput. Mientras
el botón disparador está presionado, el movimiento del lápiz se convierte en
scroll suave y sensible a la velocidad en vez de mover el cursor —
coincidiendo con el comportamiento del driver moderno (2022+)
`xf86-input-wacom`, verificado contra su código fuente.

La posición de ese segundo puntero también se mantiene sincronizada con la
del lápiz cerca de los bordes de pantalla, porque la lógica de "hot corner" /
revelado de dock de GNOME solo reacciona al movimiento del puntero tipo
"mouse" — nunca al lápiz directamente, aunque ambos mueven el mismo cursor
visible.

**Limitación cosmética conocida:** ese segundo puntero muestra brevemente su
propio ícono de cursor durante el pan y cerca de los bordes de pantalla.
Wayland no tiene una opción de "ocultar cursor" por dispositivo, así que esto
no se puede evitar del todo desde un daemon de entrada en espacio de usuario.

La historia técnica completa — cada callejón sin salida, el crash que causó
un enfoque equivocado, y por qué — está documentada en los comentarios al
inicio de [`wacom_panscroll.py`](wacom_panscroll.py).

## Alternativas consideradas

- **[OpenTabletDriver](https://opentabletdriver.net/)** — reemplazo completo
  y multiplataforma del driver de tableta. Más pesado si lo único que querés
  es pan-scroll.
- **[input-remapper](https://github.com/sezanzeb/input-remapper)** — bueno
  para remapeo discreto botón→tecla, no para gestos continuos de
  movimiento→scroll.
- **Esperar soporte nativo** — seguí los issues de GNOME linkeados arriba.

## Licencia

MIT — ver [LICENSE](LICENSE).
