# Design QA — műszaki dashboard

final result: passed

## Cél és összehasonlítás

A vizuális cél a korábban megmutatott világos, felső navigációs munkafelület (`engineering-workbench-v2.png`). A folytatás kérésére ezt az irányt vitte tovább az implementáció; számozott felhasználói választás nem történt. A tényleges alkalmazás a meglévő Python/MQTT-rendszer, nem külön statikus prototípus.

A referenciaképet és a helyi alkalmazás 1440 × 1024-es böngészőképét közös összehasonlító képen vizsgáltuk: [első összevetés](results/ui/console-redesign/comparison-first.png), [javított összevetés](results/ui/console-redesign/comparison-final.png). A 1487 × 1058-as koncepciót azonos arányú nézetre normalizáltuk, majd mindkét oldalt azonos méretben tettük a képpárra. A képek élő gyárudvari nézetet mutatnak, de az autók állapota, a szimulációs idő és az értékek nem azonosak: a koncepció illusztratív, a program képe valóban fogadott telemetriából származik. Nem állítunk adat- vagy pixelazonosságot.

## Javítási történet

1. **P2 — az első változat túl magas térképfejléce és ismétlődő érzékelési címei.** A kezdeti képen a radarnézet és a járműjelzések túl mélyre kerültek. A státusz- és nagyítási kezelőket közös sorba rendeztük, az ismétlődő címet vizuálisan elrejtettük, a hosszú mintavételi magyarázatot natív részletezőbe tettük. A végső képpáron a radar és a sebességkártya kezdete együtt látható a térképpel.
2. **P2 — a térkép csökkentett magassága túl kicsire méretezte a geometriát.** A 480 px-es felső korlátot 580 px-re módosítottuk; az eredeti geometriai arány és a natív nagyítás megmaradt. A helyazonosítók kontrasztját erősítettük. A végső összevetés már ezt mutatja.
3. **P2 — telefonon eltűnő kapcsolófeliratok és véletlenmag.** Mindkét térképréteg felirata látható, a kapcsolók külön sorba kerültek, a magmező megmaradt. A javított [mobilkép](results/ui/console-redesign/mobile.png) és a böngészős DOM-mérés igazolja. Az oldal tartalmi szélessége 375 px, a görgetési szélessége szintén 375 px a 390 px-es viewportban; a navigáció és a széles adattáblázat saját görgetést használ.
4. **P2 — navigáció aktív állapotának és ugrási viselkedésének egységesítése.** A fragmentum követése frissíti az aktív linket és az `aria-current` jelölést; a fejléc alatt helyet hagyó ugrás működik. A márkajel a teljes munkafelület tetejére vezet. A natív vezérlők és lenyitható panelek megmaradtak.

## Ellenőrzött felületek

- **Tipográfia:** helyben beágyazott Inter változó font, egységes számtördelés és címsorhierarchia. A műszaki részletek szándékosan sűrűbbek a fő eredménymutatóknál. A végső képen nincs levágott főcím vagy ütköző vezérlő.
- **Elrendezés:** sötét felső navigáció, kompakt vezérlés és KPI-sáv, domináns térkép, jobb oldali járművizsgáló, külön sebességidősor. A következő futás terve külön szakaszba került, megkülönböztetve az élő állapottól. A [tabletkép](results/ui/console-redesign/tablet.png) 900 × 1000-es nézetben is használható elrendezést mutat.
- **Szín és alkatrészek:** fehér/slate kártyák, visszafogott kék kiemelés, egységes szegélyek és rádiuszok. A riasztási és adatfrissességi színek jelentése megmaradt. A közelségnézet sötét műszer maradt; ez szándékos eltérés az illusztratív világos radartól.
- **Eszközök és képek:** a meglévő natív SVG-adatvizualizáció maradt használatban. A Tabler ikonok és az Inter font helyben megtalálhatók, licencekkel és forráslenyomatokkal. A koncepció autóképei és fiktív környezeti objektumai nem kerültek mintaként a programba.
- **Szöveg és adatok:** a működő funkciók nevei, az egységek, az archívhatárok és a szimulációs korlátok megmaradtak. Az illusztratív KPI-k helyett kizárólag az aktuális futás értékei szerepelnek. Nincs megtévesztő fizikai kamerakép vagy biztonsági tanúsítási állítás.

## Működés és bizonyíték

A [böngészős nyugta](results/ui/console-redesign/browser-check.json) rögzíti a szünet/folytatás, járműváltás, érzékelési részletek, beállításnyitás és navigáció ellenőrzését. Az archivált `d8245dee142a` futás `CAR001` járművéhez 2000 / 10 578 fogadott keret betöltődött; a legerősebb fékezés gomb a 56,25 s-os keretre ugrott, és a fékezési panel ezt mutatta. [Archív kép](results/ui/console-redesign/archive.png).

A konzolban nem volt figyelmeztetés vagy hiba. A végső forrásokra futtatott 313 meglévő felületi ellenőrzés sikeres; a 2486 korábbi MQTT-keret offline vizsgálata két ablakban szintén sikeres. Mind a 428 eredeti DOM-azonosító megmaradt, nincs ismétlődő azonosító. A 20 mért forrás és konfiguráció változatlan. Ezek felületi ellenőrzések; új statisztikai vagy brokeres kampány nem történt.

## Maradó, elfogadott eltérések

A működő alkalmazás több bizonyítékot és diagnosztikát tartalmaz a koncepciónál, ezért a fejléc alatti információs rész sűrűbb, a teljes oldal hosszabb. Az élő adatfolyam és a megőrzött, valódi geometria elsőbbséget kap az illusztratív autóelhelyezéssel szemben. A radar sötét háttere, az eredeti sematikus autók és a távolsági küszöbök szándékosan megmaradtak. Ezek nem nyitott működési hibák. További P0/P1/P2 probléma a végső összevetés és az ellenőrzött munkafolyamatok során nem maradt.


## Kiegészítő javítási kör — 2026. október 19.

final result: passed

Az új audit friss böngészőképeken reprodukálta a grafikon túlcsordulását és a keskeny útelőzményt. A korábbi nyugta az előző forrásállapot történeti eredménye marad; ezt a kört külön ellenőrzési csomag rögzíti.

- **P2, javítva:** a 150 px-es keretbe nőtt 295 px-es SVG helyett a grafikon és a keret magassága egyaránt 180 px. A képaláírás 10 px-rel az SVG után kezdődik. A szélességet a tényleges rendelkezésre álló helyhez illesztjük; a cím az autóazonosítót is tartalmazza.
- **P2, javítva:** a mentett út többé nem szorul az élő térkép bal oszlopába. Azonos archivált jármű és mintahatár, azonos 1440 × 1024-es viewport mellett készült a [közös előtte/utána összevetés](results/ui/console-polish/comparison-trip.png). Az élő flotta külön kártya; telefonon a DOM és a vizuális sorrend is a térkép után hozza a járművizsgálót.
- **P2, javítva:** az aktív navigáció görgetéskor is frissül. Az oldal vége és az egymás mellett megjelenő térkép/járművizsgáló külön esetet kap. A billentyűs átugró link a fő tartalomra fókuszál, a sebességgombok kijelölt állapota felolvasható.
- **P2, javítva:** a széles, magasságban korlátozott SVG jelöléseinek méretezése mindkét tengelyt figyelembe veszi. A távolsági magyarázat a nézeten belülre került az élő és mentett térképen is. Kicsinyített nézetben a nem kijelölt autófeliratok fókuszra, érintésre/kijelölésre vagy nagyításra érhetők el.

A meglévő Inter/Tabler arculat, színek és adatgrafikák maradtak. A címsorok, egységek és mentett adatjelentés nem változott. A [mobilos képpár](results/ui/console-polish/comparison-mobile.png) azonos 390 × 844-es viewportban, járművizsgáló állapotban készült; az autó és a szimulációs idő különbözik, így a képpár csak vizuális összevetés. A részletes műszaki szöveg sűrűsége szándékos maradt.

[Böngészős ellenőrzés](results/ui/console-polish/browser-check.json): 390/900/1440/1920 px, járműváltás, sebességkérés, görgetést követő navigáció, billentyűs belépés, teljes szélességű archív út és pontos fékezési keret. Nincs egész oldalas vízszintes túlcsordulás, nincs böngészőkonzol-hiba vagy figyelmeztetés. A friss regressziós nyugta 336 sikeres esetet és 2486 korábbi MQTT-keret két offline ablakának vizsgálatát tartalmazza. A 431 korábbi DOM-azonosító és a 20 mért forrás/config lenyomata megmaradt.

A végső képpárokon és az ellenőrzött folyamatokban további P0/P1/P2 megjelenítési vagy működési hiba nem maradt. Ez nem teljes akadálymentességi tanúsítás és nem új fizikai vagy statisztikai kampány.
