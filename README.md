# wacom-panscroll

Reemplazo de `xsetwacom ... Button N "pan"` para Wayland/GNOME, donde ni
libinput ni mutter implementan pan-scroll de forma nativa (ver investigación
en el historial del chat: [gnome-control-center#1645](https://gitlab.gnome.org/GNOME/gnome-control-center/-/issues/1645),
[gtk#5570](https://gitlab.gnome.org/GNOME/gtk/-/issues/5570)).

## Qué hace

Un daemon en userspace (`wacom_panscroll.py`) que:

1. Agarra el dispositivo real "Wacom Intuos S Pen" de forma exclusiva (`EVIOCGRAB`).
2. Crea un dispositivo virtual vía `uinput` que clona sus capacidades (mismo
   vendor/product, mismos rangos de ejes) más `REL_WHEEL`/`REL_HWHEEL`.
   udev/libwacom lo reconocen como parte de la misma tableta física — no
   aparece como un dispositivo duplicado (verificado con
   `libwacom-list-local-devices`).
3. Reenvía todos los eventos normales 1:1 al clon (dibujo, presión, cursor
   sin cambios). Mientras el botón lateral superior del lápiz (`BTN_STYLUS2`)
   está presionado, el movimiento del lápiz se convierte en scroll
   (`REL_WHEEL`/`REL_HWHEEL`) en vez de mover el cursor, y el propio evento
   de botón no se reenvía (no dispara su acción por defecto).

## Instalación

Ya instalado como servicio `systemd --user`:

```bash
systemctl --user status wacom-panscroll.service
journalctl --user -u wacom-panscroll.service -f
```

El archivo `wacom-panscroll.service` está symlinkeado en
`~/.config/systemd/user/`, así que cualquier cambio en este repo se aplica
con:

```bash
systemctl --user daemon-reload
systemctl --user restart wacom-panscroll.service
```

## Configuración

Variables de entorno (editar `ExecStart` en `wacom-panscroll.service` o
agregar `Environment=` si hace falta ajustar):

| Variable | Default | Descripción |
|---|---|---|
| `WACOM_PANSCROLL_DEVICE` | `Wacom Intuos S Pen` | nombre exacto del dispositivo a interceptar |
| `WACOM_PANSCROLL_BUTTON` | `stylus2` | `stylus` (botón inferior) o `stylus2` (botón superior) — cuál botón activa el pan |
| `WACOM_PANSCROLL_THRESHOLD` | `300` | unidades de movimiento del lápiz por "click" de scroll (más bajo = más rápido/sensible) |
| `WACOM_PANSCROLL_INVERT_Y` | `0` | `1` para invertir el scroll vertical |
| `WACOM_PANSCROLL_HSCROLL` | `1` | `0` para desactivar el scroll horizontal (solo vertical) |

## Desinstalar / desactivar

```bash
systemctl --user disable --now wacom-panscroll.service
rm ~/.config/systemd/user/wacom-panscroll.service
systemctl --user daemon-reload
```

No deja rastro en el sistema (sin reglas udev, sin cambios de permisos): al
apagar el servicio el dispositivo real vuelve a funcionar exactamente igual
que antes.

## Notas

- Si en algún momento GNOME/mutter agregan soporte nativo de pan (los
  tickets de arriba siguen sin resolver a la fecha de esta implementación),
  este daemon se puede desactivar sin dejar nada pendiente.
- La carpeta `spike/` tiene el script usado para validar que el clon uinput
  es reconocido correctamente como la misma tableta física antes de
  construir el daemon completo.
