#!/usr/bin/env python3
"""Options-row text: a title and a two-line description per row, and the labels of rows with more than
On/Off, in the game's ten message folders.

Index = the game's language word (0x1614B34): 0 JPN (kana), 1 JPN_KANJI, 2 English, 3 French, 4 Italian,
5 German, 6 Spanish, 7 Korean, 8 Simp_Chinese, 9 Trad_Chinese. "\n" is the game's own line break; the
Japanese text uses the game's full-width spaces. "On" and "Off" are the game's own translated labels, so
they are not here. Lengths stay within the game's own Options text per language (tests/test_options.py).
English is the reference; the other languages are our translations (corrections welcome).
"""
LANGS = ['JPN', 'JPN_KANJI', 'English', 'French', 'Italian', 'German', 'Spanish', 'Korean',
         'Simp_Chinese', 'Trad_Chinese']

TEXT = {
    'wild': [
        ('やせいとの　バトル', 'やせいの　ポケモンと　バトルして\nけいけんちを　もらえます'),
        ('野生との　バトル', '野生の　ポケモンと　バトルして\n経験値を　もらえます'),
        ('Wild Battles', 'Battle wild Pokémon and earn Exp. Points.\nWhen it’s off, you catch them as usual.'),
        ('Combats sauvages', 'Affrontez les Pokémon sauvages pour gagner\nde l’Exp. Sinon, capturez-les comme d’habitude.'),
        ('Lotte selvatiche', 'Lotta con i Pokémon selvatici e ottieni Esp.\nSe disattivata, li catturi come al solito.'),
        ('Wilde Kämpfe', 'Kämpfe gegen wilde Pokémon, um E.-Punkte zu\nerhalten. Aus: Fangen wie gewohnt.'),
        ('Combates salvajes', 'Lucha contra Pokémon salvajes y gana Exp.\nSi se desactiva, se capturan como siempre.'),
        ('야생 배틀', '야생 포켓몬과 배틀하여 경험치를 얻습니다.\n끄면 평소처럼 잡습니다.'),
        ('野生对战', '与野生宝可梦对战，\n并获得经验值。'),
        ('野生對戰', '與野生寶可夢對戰，\n並獲得經驗值。'),
    ],
    'quick': [
        ('クイックキャッチ', 'HPが　えらんだ　わりあい　いかの\nポケモンを　Yボタンで　つかまえる'),
        ('クイックキャッチ', 'HPが　選んだ　割合　以下の\nポケモンを　Yボタンで　捕まえる'),
        ('Quick Catch', 'Press Y to catch a wild Pokémon whose HP is\nat or below the chosen level.'),
        ('Capture rapide', 'Appuyez sur Y pour capturer un Pokémon\nsauvage dont les PV sont sous le seuil choisi.'),
        ('Cattura rapida', 'Premi Y per catturare un Pokémon selvatico\ncon PS pari o sotto la soglia scelta.'),
        ('Schnellfang', 'Drücke Y, um ein wildes Pokémon zu fangen,\nwenn seine KP unter dem Wert liegen.'),
        ('Captura rápida', 'Pulsa Y para capturar un Pokémon salvaje con\nlos PS por debajo del nivel elegido.'),
        ('퀵 캐치', 'HP가 선택한 비율 이하인 야생\n포켓몬을 Y버튼으로 잡습니다'),
        ('快速捕捉', 'HP在所选比例以下时，\n可按Y键捕捉野生宝可梦。'),
        ('快速捕捉', 'HP在所選比例以下時，\n可按Y鍵捕捉野生寶可夢。'),
    ],
    'share': [
        ('がくしゅうそうち', 'けいけんちを　もらう　ポケモンを\nえらべます'),
        ('学習装置', '経験値を　もらう　ポケモンを\n選べます'),
        ('Exp. Share', 'Off: only the Pokémon that battled get Exp.\nTrainers: the whole party, in Trainer battles.'),
        ('Multi Exp', 'Choisissez quels Pokémon gagnent de l’Exp.\nDresseurs : toute l’équipe contre les Dresseurs.'),
        ('Condividi Esp.', 'Scegli quali Pokémon ottengono Punti Esperienza.\nAllenatori: tutta la squadra contro Allenatori.'),
        ('EP-Teiler', 'Lege fest, welche Pokémon E.-Punkte erhalten.\nTrainer: das ganze Team in Trainerkämpfen.'),
        ('Repartir Exp', 'Elige qué Pokémon reciben puntos de experiencia.\nEntrenador: todo el equipo contra Entrenadores.'),
        ('학습장치', '경험치를 받을 포켓몬을\n선택할 수 있습니다'),
        ('学习装置', '可以选择获得经验值的\n宝可梦。'),
        ('學習裝置', '可選擇獲得經驗值的\n寶可夢。'),
    ],
    'catch_exp': [
        ('つかまえた　けいけんち', 'つかまえたとき　てもちが\nけいけんちを　もらうか　えらべます'),
        ('捕獲の　経験値', '捕まえたとき　手持ちが\n経験値を　もらうか　選べます'),
        ('Catch Exp.', 'You can choose whether catching a Pokémon\ngives your party Exp. Points.'),
        ('Exp. de capture', 'Choisissez si la capture d’un Pokémon\ndonne de l’Exp. à votre équipe.'),
        ('Esp. cattura', 'Scegli se catturare un Pokémon fa ottenere\nPunti Esperienza alla tua squadra.'),
        ('Fang-E.-Punkte', 'Lege fest, ob dein Team E.-Punkte erhält,\nwenn du ein Pokémon fängst.'),
        ('Exp. al capturar', 'Elige si capturar un Pokémon da puntos\nde experiencia a tu equipo.'),
        ('포획 경험치', '포켓몬을 잡았을 때 지닌 포켓몬이\n경험치를 받을지 선택합니다.'),
        ('捕捉经验值', '可以选择捕捉宝可梦时\n队伍是否获得经验值。'),
        ('捕捉經驗值', '可選擇捕捉寶可夢時\n隊伍是否獲得經驗值。'),
    ],
    'timer': [
        ('バトルの　じかんせいげん', 'オンのとき　やせいの　バトルに\n5ふんの　じかんせいげんが　つく'),
        ('バトルの　制限時間', 'オンのとき　野生の　バトルに\n5分の　制限時間が　つく'),
        ('Battle Timer', 'When it’s on, wild battles have the game’s\nusual 5-minute time limit.'),
        ('Chrono de combat', 'Si activé, les combats sauvages ont la limite\nhabituelle de 5 minutes.'),
        ('Timer lotta', 'Se attivo, le lotte con i Pokémon selvatici\nhanno il solito limite di 5 minuti.'),
        ('Kampf-Zeitlimit', 'Wenn an, haben Kämpfe gegen wilde Pokémon\ndas übliche Zeitlimit von 5 Minuten.'),
        ('Tiempo límite', 'Si se activa, los combates salvajes tienen el\nlímite habitual de 5 minutos.'),
        ('배틀 제한 시간', 'On일 때 야생 포켓몬과의 배틀에\n5분의 제한 시간이 생깁니다'),
        ('对战时间限制', '开启时，与野生宝可梦的\n对战有5分钟的时间限制。'),
        ('對戰時間限制', '開啟時，與野生寶可夢的\n對戰有5分鐘的時間限制。'),
    ],
    'marker': [
        ('つかまえた　しるし', 'つかまえた　ポケモンの　HPに\nボールの　しるしが　でます'),
        ('捕まえた　しるし', '捕まえた　ポケモンの　HPに\nボールの　しるしが　出ます'),
        ('Caught Marker', 'Shows a Poké Ball beside a wild Pokémon’s HP\nbox if you have caught that species.'),
        ('Pokémon capturé', 'Affiche une Poké Ball près des PV d’un Pokémon\nsauvage déjà capturé.'),
        ('Segno cattura', 'Mostra una Poké Ball accanto ai PS di un\nPokémon selvatico già catturato.'),
        ('Gefangen-Symbol', 'Zeigt einen Pokéball neben den KP eines\nwilden Pokémon, das du schon gefangen hast.'),
        ('Marca de captura', 'Muestra una Poké Ball junto a los PS de un\nPokémon salvaje que ya has capturado.'),
        ('포획 마크', '잡은 적이 있는 포켓몬의 HP 옆에\n몬스터볼 마크가 나옵니다'),
        ('捕捉标记', '捕捉过的宝可梦的HP旁\n会显示精灵球标记。'),
        ('捕捉標記', '捕捉過的寶可夢的HP旁\n會顯示精靈球標記。'),
    ],
    'recall': [
        ('ボールに　もどす', 'オンのとき　たおれた　やせいの\nポケモンが　ボールに　もどる'),
        ('ボールに　戻す', 'オンのとき　倒れた　野生の\nポケモンが　ボールに　戻る'),
        ('Return to Ball', 'When it’s on, a fainted wild Pokémon goes\nback into its Poké Ball.'),
        ('Retour en Ball', 'Si activé, un Pokémon sauvage K.O. retourne\ndans sa Poké Ball.'),
        ('Ritorno in Ball', 'Se attivo, un Pokémon selvatico esausto\ntorna nella sua Poké Ball.'),
        ('Rückruf in Ball', 'Wenn an, kehrt ein besiegtes wildes Pokémon\nin seinen Pokéball zurück.'),
        ('Volver a la Ball', 'Si se activa, un Pokémon salvaje debilitado\nvuelve a su Poké Ball.'),
        ('볼로 돌아가기', 'On일 때 쓰러진 야생 포켓몬이\n몬스터볼로 돌아갑니다'),
        ('回到精灵球', '开启时，倒下的野生宝可梦\n会回到精灵球里。'),
        ('回到精靈球', '開啟時，倒下的野生寶可夢\n會回到精靈球裡。'),
    ],
    'trade': [
        ('つうしんしんか', 'つうしんで　しんかする　ポケモンが\nえらんだ　レベルから　しんかする'),
        ('通信進化', '通信で　進化する　ポケモンが\n選んだ　レベルから　進化する'),
        ('Trade Evolution', 'Pokémon that evolve by trading also evolve\nby levelling up from the chosen level.'),
        ('Évolution échange', 'Les Pokémon qui évoluent par échange évoluent\naussi en montant au niveau choisi.'),
        ('Evoluz. scambio', 'I Pokémon che si evolvono con lo scambio si\nevolvono anche salendo al livello scelto.'),
        ('Tausch-Entwicklung', 'Pokémon, die sich durch Tausch entwickeln,\nentwickeln sich auch ab dem gewählten Level.'),
        ('Evo. intercambio', 'Los Pokémon que evolucionan por intercambio\nevolucionan también al llegar al nivel elegido.'),
        ('통신진화', '통신교환으로 진화하는 포켓몬이\n선택한 레벨부터 진화합니다'),
        ('通信进化', '通过通信交换进化的宝可梦\n在所选等级以上升级时进化。'),
        ('通信進化', '透過通信交換進化的寶可夢\n在所選等級以上升級時進化。'),
    ],
}

# The button on the game's Options page that opens the mod's own page (like "Open Controller Settings").
PAGE = [
    ('やせいバトルの　せってい', 'やせいバトルの　せっていを\nかえることが　できます'),
    ('野生バトルの　設定', '野生バトルの　設定を\n変えることが　できます'),
    ('Wild Battles Settings', 'Change the settings of Wild Battles Remastered:\nwild battles, catching, Exp. and more.'),
    ('Options combats sauvages', 'Modifiez les réglages de Wild Battles\nRemastered : combats, capture, Exp., etc.'),
    ('Impostazioni lotte', 'Cambia le impostazioni di Wild Battles\nRemastered: lotte, catture, Esp. e altro.'),
    ('Wilde Kämpfe: Optionen', 'Ändere die Einstellungen von Wild Battles\nRemastered: Kämpfe, Fangen, E.-Punkte usw.'),
    ('Opciones combates', 'Cambia los ajustes de Wild Battles\nRemastered: combates, capturas, Exp. y más.'),
    ('야생 배틀 설정', '야생 배틀의 설정을\n바꿀 수 있습니다'),
    ('野生对战设置', '可以更改野生对战的\n相关设置。'),
    ('野生對戰設定', '可以更改野生對戰的\n相關設定。'),
]

# The page row's value: press left or right on it to open the page.
PAGE_OPEN = ['ひらく', '開く', 'Open', 'Ouvrir', 'Apri', 'Öffnen', 'Abrir', '열기', '打开', '打開']

# Labels of the values that are not plain On/Off: row -> {value: [10 strings]}. Value 0 is always the
# game's "Off" and, for two-value rows, value 1 the game's "On".
def _lv(n):
    return ['Lv.%d' % n, 'Lv.%d' % n, 'Lv. %d' % n, 'N. %d' % n, 'Lv. %d' % n, 'Lv. %d' % n, 'Nv. %d' % n,
            'Lv.%d' % n, '%d级' % n, '%d級' % n]

VALUES = {
    'quick': {1: ['25％', '25％', '25%', '25 %', '25%', '25 %', '25 %', '25%', '25％', '25％'],
              2: ['50％', '50％', '50%', '50 %', '50%', '50 %', '50 %', '50%', '50％', '50％'],
              3: ['いつでも', 'いつでも', 'Any HP', 'Tous PV', 'Sempre', 'Immer', 'Siempre', '항상', '随时', '隨時']},
    'share': {1: ['トレーナー', 'トレーナー', 'Trainers', 'Dresseurs', 'Allenatori', 'Trainer', 'Entrenador',
                  '트레이너', '训练家', '訓練家'],
              2: None},          # "On"
    'trade': {1: _lv(30), 2: _lv(40), 3: _lv(50)},
}

assert all(len(v) == len(LANGS) for v in TEXT.values()) and len(PAGE) == len(LANGS)
assert all(s is None or len(s) == len(LANGS) for d in VALUES.values() for s in d.values())
