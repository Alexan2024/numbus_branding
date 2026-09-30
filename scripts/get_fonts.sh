#!/bin/sh
# Шрифты каталога (лицензия OFL) с зафиксированного коммита Google Fonts:
# сборка не сломается, если у них что-то переименуют. Запуск: sh scripts/get_fonts.sh [папка]
set -eu
DIR="${1:-fonts}"
GF=https://raw.githubusercontent.com/google/fonts/23e54b51ddffbc7713c583748e3bd86f62b1fa4a/ofl
mkdir -p "$DIR"
get() { curl -fsSL --retry 3 "$GF/$1" -o "$DIR/$2"; }
get "inter/Inter%5Bopsz,wght%5D.ttf"                        Inter.ttf
get "onest/Onest%5Bwght%5D.ttf"                             Onest.ttf
get "golostext/GolosText%5Bwght%5D.ttf"                     GolosText.ttf
get "manrope/Manrope%5Bwght%5D.ttf"                         Manrope.ttf
get "geologica/Geologica%5BCRSV,SHRP,slnt,wght%5D.ttf"      Geologica.ttf
get "montserrat/Montserrat%5Bwght%5D.ttf"                   Montserrat.ttf
get "jost/Jost%5Bwght%5D.ttf"                               Jost.ttf
get "rubik/Rubik%5Bwght%5D.ttf"                             Rubik.ttf
get "nunito/Nunito%5Bwght%5D.ttf"                           Nunito.ttf
get "comfortaa/Comfortaa%5Bwght%5D.ttf"                     Comfortaa.ttf
get "unbounded/Unbounded%5Bwght%5D.ttf"                     Unbounded.ttf
get "oswald/Oswald%5Bwght%5D.ttf"                           Oswald.ttf
get "playfairdisplay/PlayfairDisplay%5Bwght%5D.ttf"         PlayfairDisplay.ttf
get "cormorantgaramond/CormorantGaramond%5Bwght%5D.ttf"     CormorantGaramond.ttf
get "lora/Lora%5Bwght%5D.ttf"                               Lora.ttf
get "jetbrainsmono/JetBrainsMono%5Bwght%5D.ttf"             JetBrainsMono.ttf
echo "Шрифты: $(ls "$DIR" | wc -l) файлов в $DIR"
