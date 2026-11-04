#!/usr/bin/env python3
"""Transforme une image de logo (tuile sur fond uni) en icône macOS 1024 px.

1. Détecte la tuile en la comparant à la couleur du fond (coins de l'image).
2. La recadre en carré et l'arrondit (forme d'icône macOS, sans le fond ni l'ombre).
3. La place sur une toile transparente de 1024 px, à 824 px (grille d'icônes d'Apple).

Usage : python3 preparer_icone.py source.png sortie.png
"""
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

TOILE, TUILE = 1024, 824
RAYON = 0.225  # arrondi des icônes macOS (≈ 22,5 % du côté)

source = Image.open(sys.argv[1]).convert("RGB")
px = np.asarray(source).astype(int)

# Couleur du fond : moyenne des quatre coins
h, w, _ = px.shape
coins = np.concatenate([px[:20, :20], px[:20, -20:], px[-20:, :20], px[-20:, -20:]]).reshape(-1, 3)
fond = coins.mean(axis=0)

# Tuile = pixels nettement colorés (la tuile est rouge, le fond gris clair)
r, g, b = px[..., 0], px[..., 1], px[..., 2]
saturation = px.max(axis=2) - px.min(axis=2)
masque = (saturation > 60) & (np.abs(px - fond).sum(axis=2) > 90)
lignes, colonnes = np.where(masque)
haut, bas = np.percentile(lignes, [0.5, 99.5]).astype(int)
gauche, droite = np.percentile(colonnes, [0.5, 99.5]).astype(int)

# Carré centré sur la tuile, légèrement rentré pour retirer le bord flou
cx, cy = (gauche + droite) / 2, (haut + bas) / 2
cote = min(droite - gauche, bas - haut) * 0.985
boite = (int(cx - cote / 2), int(cy - cote / 2), int(cx + cote / 2), int(cy + cote / 2))
tuile = source.crop(boite).resize((TUILE, TUILE), Image.LANCZOS)

# Arrondi macOS, dessiné en 4x puis réduit pour des bords lisses
grand = TUILE * 4
forme = Image.new("L", (grand, grand), 0)
ImageDraw.Draw(forme).rounded_rectangle([0, 0, grand - 1, grand - 1], radius=int(grand * RAYON), fill=255)
forme = forme.resize((TUILE, TUILE), Image.LANCZOS)

icone = Image.new("RGBA", (TOILE, TOILE), (0, 0, 0, 0))
# Ombre portée discrète, comme les icônes du Dock
ombre = Image.new("RGBA", (TOILE, TOILE), (0, 0, 0, 0))
ombre.paste((0, 0, 0, 70), (100, 112), forme)
icone.alpha_composite(ombre.filter(ImageFilter.GaussianBlur(14)))
tuile_rgba = tuile.convert("RGBA")
tuile_rgba.putalpha(forme)
icone.alpha_composite(tuile_rgba, (100, 100))
icone.save(sys.argv[2])
print(f"Icône créée : tuile détectée {boite}, sortie {sys.argv[2]}")
