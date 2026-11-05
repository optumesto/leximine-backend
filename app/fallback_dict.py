# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""A small built-in es_es dictionary so the backend runs with zero setup.

These strings are typed from memory and are *approximate*. Run
`scripts/extract_lang.py` to pull the authoritative es_es.json out of your own
Minecraft assets -- real entries always override anything in here.
"""

from __future__ import annotations

FALLBACK: dict[str, dict[str, str]] = {
    "es_es": {
        # ores / materials
        "item.minecraft.diamond": "Diamante",
        "item.minecraft.emerald": "Esmeralda",
        "item.minecraft.coal": "Carbón",
        "item.minecraft.iron_ingot": "Lingote de hierro",
        "item.minecraft.gold_ingot": "Lingote de oro",
        "item.minecraft.copper_ingot": "Lingote de cobre",
        "item.minecraft.redstone": "Redstone",
        "item.minecraft.lapis_lazuli": "Lapislázuli",
        "item.minecraft.flint": "Pedernal",
        "item.minecraft.clay_ball": "Arcilla",
        # basics
        "item.minecraft.stick": "Palo",
        "item.minecraft.string": "Cuerda",
        "item.minecraft.feather": "Pluma",
        "item.minecraft.leather": "Cuero",
        "item.minecraft.bone": "Hueso",
        "item.minecraft.egg": "Huevo",
        "item.minecraft.paper": "Papel",
        "item.minecraft.book": "Libro",
        "item.minecraft.bucket": "Cubo",
        "item.minecraft.gunpowder": "Pólvora",
        # food
        "item.minecraft.apple": "Manzana",
        "item.minecraft.bread": "Pan",
        "item.minecraft.wheat": "Trigo",
        "item.minecraft.carrot": "Zanahoria",
        "item.minecraft.potato": "Patata",
        "item.minecraft.cooked_beef": "Bistec",
        "item.minecraft.golden_apple": "Manzana dorada",
        # tools / weapons
        "item.minecraft.bow": "Arco",
        "item.minecraft.arrow": "Flecha",
        "item.minecraft.wooden_pickaxe": "Pico de madera",
        "item.minecraft.stone_pickaxe": "Pico de piedra",
        "item.minecraft.iron_pickaxe": "Pico de hierro",
        "item.minecraft.diamond_pickaxe": "Pico de diamante",
        "item.minecraft.diamond_sword": "Espada de diamante",
        "item.minecraft.iron_sword": "Espada de hierro",
        "item.minecraft.shears": "Tijeras",
        # blocks
        "block.minecraft.stone": "Piedra",
        "block.minecraft.cobblestone": "Roca",
        "block.minecraft.dirt": "Tierra",
        "block.minecraft.grass_block": "Bloque de hierba",
        "block.minecraft.sand": "Arena",
        "block.minecraft.gravel": "Grava",
        "block.minecraft.oak_log": "Tronco de roble",
        "block.minecraft.oak_planks": "Tablones de roble",
        "block.minecraft.glass": "Vidrio",
        "block.minecraft.torch": "Antorcha",
        "block.minecraft.chest": "Cofre",
        "block.minecraft.furnace": "Horno",
        "block.minecraft.crafting_table": "Mesa de trabajo",
        "block.minecraft.diamond_block": "Bloque de diamante",
        "block.minecraft.iron_ore": "Mena de hierro",
        "block.minecraft.coal_ore": "Mena de carbón",
        "block.minecraft.obsidian": "Obsidiana",
        "block.minecraft.water": "Agua",
    }
}
