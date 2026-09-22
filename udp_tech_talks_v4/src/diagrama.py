"""
Diagramas de flujo del proceso, pensados para mostrarse en pantalla durante el
evento (pestaña "Cómo funciona"). Se dibujan con matplotlib (sin dependencias
externas ni conexión a internet) para que nunca fallen en el Chromebox.
"""

import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch

COLORES = {
    "gris": ("#F1EFE8", "#5F5E5A", "#2C2C2A"),  # fondo, borde, texto
    "teal": ("#E1F5EE", "#0F6E56", "#04342C"),
    "purpura": ("#EEEDFE", "#534AB7", "#26215C"),
    "coral": ("#FAECE7", "#993C1D", "#4A1B0C"),
}


def _dibujar_flujo(ax, etapas):
    """
    etapas: lista de tuplas (titulo, subtitulo, color_key).
    Dibuja las etapas apiladas verticalmente con flechas entre ellas.
    """
    n = len(etapas)
    alto_caja = 1.0
    espacio = 0.6
    alto_total = n * alto_caja + (n - 1) * espacio

    ax.set_xlim(0, 10)
    ax.set_ylim(0, alto_total + 1)
    ax.axis("off")

    y = alto_total
    centros_y = []
    for titulo, subtitulo, color_key in etapas:
        fondo, borde, texto = COLORES[color_key]
        caja = FancyBboxPatch(
            (1.5, y - alto_caja),
            7.0,
            alto_caja,
            boxstyle="round,pad=0.05,rounding_size=0.15",
            linewidth=1.2,
            edgecolor=borde,
            facecolor=fondo,
        )
        ax.add_patch(caja)
        centro_y = y - alto_caja / 2
        ax.text(5.0, centro_y + 0.15, titulo, ha="center", va="center", fontsize=12, color=texto, weight="bold")
        ax.text(5.0, centro_y - 0.2, subtitulo, ha="center", va="center", fontsize=9.5, color=texto)
        centros_y.append((y, y - alto_caja))
        y -= alto_caja + espacio

    for i in range(len(centros_y) - 1):
        y_inicio = centros_y[i][1]
        y_fin = centros_y[i + 1][0]
        flecha = FancyArrowPatch(
            (5.0, y_inicio), (5.0, y_fin), arrowstyle="-|>", mutation_scale=15, linewidth=1.2, color="#5F5E5A"
        )
        ax.add_patch(flecha)


def figura_flujo_matchmaking():
    """Flujo: fuentes de datos -> Jev -> agente generador -> pantalla."""
    fig, ax = plt.subplots(figsize=(5, 6))
    etapas = [
        ("Luma + check-in en vivo", "se unifican en registrants.json", "gris"),
        ("Jev (opcional)", "clasifica: elige la charla más afín", "teal"),
        ("Agente generador", "OpenRouter o modo local", "purpura"),
        ("En pantalla", "charla + razonamiento + mapa", "coral"),
    ]
    _dibujar_flujo(ax, etapas)
    ax.set_title("Matchmaking en vivo", fontsize=13, weight="bold", pad=10)
    fig.tight_layout()
    return fig


def figura_flujo_cierre():
    """Flujo: estación estudiantes -> moderación -> muro público -> cierre de jornada."""
    fig, ax = plt.subplots(figsize=(5, 6))
    etapas = [
        ("Estación estudiantes", "responden la pregunta ancla", "gris"),
        ("Jev (opcional)", "filtra antes de publicar", "teal"),
        ("Muro público", "respuestas visibles en vivo", "coral"),
        ("Cierre de jornada", "el agente redacta la noticia", "purpura"),
    ]
    _dibujar_flujo(ax, etapas)
    ax.set_title("Estudiantes y cierre", fontsize=13, weight="bold", pad=10)
    fig.tight_layout()
    return fig
