Drop Manrope and JetBrains Mono here to get the exact designed type.

EasyAI registers every .ttf / .otf in this folder at startup and uses them if
they are Manrope (text) or JetBrains Mono (numbers). Without them it falls back
to Segoe UI and Cascadia Mono, which are already on every modern Windows PC -
the layout is unaffected either way.

Both fonts are SIL Open Font License, so they can be shipped with EasyAI:
  Manrope         https://fonts.google.com/specimen/Manrope
  JetBrains Mono  https://www.jetbrains.com/lp/mono/
