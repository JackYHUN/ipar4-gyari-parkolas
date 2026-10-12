# Beadandó: bemutató és mérnöki bizonyíték

A 2026. október 12-i ellenőrzés hat előre meghatározott forgatókönyve sikeres. Három külön, tényleges loopback MQTT/HTTP futás készült a témalapi hat autós, tizenhat parkolóhelyes, két zebrás modellen; mellettük tizennégy név szerint futtatott koordinátorteszt ad célzott foglalási bizonyítékot. A normál működéshez külön, hibainjektálás nélküli futás tartozik. A hálózati kampány nem helyettesíti a stratégiák páros statisztikai összehasonlítását.

Az ellenőrzés belépőpontja: [`results/course-verification-v18/summary.json`](../results/course-verification-v18/summary.json). Ez a két megőrzött jelentés SHA-256 lenyomatával azonosítja a bizonyítékcsomagot; S01-hez a külön névleges futást, S02–S06-hoz az összetett kampányt rendeli. A futások meglévő alkalmazáskódot használnak; az ellenőrző program nem szúr be adatbázisrekordot, nem módosítja a koordinátor vagy a mozgásmodell belső állapotát.

## Konfiguráció, dátum és rendszerhatár

Az alap a `config/default.json`, SHA-256: `03e055f8c5b093f1eb6d2d406691257fc056d1df794c88609fb5e1fdd6428907`. A geometria, a 0,05 s-os modelllépés, a 0,1 s-os névleges érzékelési periódus, a 3 m/s legnagyobb sebesség, a 2 m/s² fékezés, a 0,5 s-os helyi érzékelőfrissességi határ, valamint a 6/16/2 kapacitás változatlan.

A kampány eltérései kifejezettek: a termelés hat autóra véges, a parkolási tartózkodás 1000 szimulációs másodperc, az automatikus gyalogosérkezés kikapcsolt, az eseményeket kijelölt időpontban a publikus HTTP vezérlés indítja. A névleges és hibafutás négy kezdetben foglalt helyet használ; a telt eset mind a tizenhatot. A mag mindhárom futásban 123, a kiosztás `nearest`. A broker és a HTTP-szerver külön szabad, csak `127.0.0.1` címhez tartozó portot kapott. Az alkalmazás, a broker, a szimulátor és a koordinátor valóban külön szoftverfolyamat; a megfigyelő és a kiegészítő rögzítő külön MQTT-kliens.

| Bizonyítékfutás | Futásazonosító | Ellenőrzés lezárása (UTC) | Host idő | Gyorsítás | Elsődleges eredmény |
| --- | --- | --- | ---: | --- | --- |
| Külön normál futás | `a8d33ff8fef3` | 2026-10-12 07:18:34.820 | 33,157 s | 8× | 6/6 beparkolt; 0 referenciaütközés; 0 gyalogosmegállás |
| Idempotencia, zebra és hibák | `6d682f18f3dc` | 2026-10-12 07:15:50.663, a kétesetes kampány lezárásakor | A hibafutás és telt eset együtt 52,531 s | Kezdetben 4×, később 8× | 6/6 beparkolt helyreállás után; 0 referenciaütközés; 1 gyalogosmegállás |
| Telt parkoló | `487209a72172` | A fenti kampány része | A fenti kampány része | 8× | 16 foglalt hely; 1 kijárati autó áll; 5 a termelési pufferben; 0 foglalás |

A dátumok a jelentés generálási órái; az egyes események saját fogadási UTC idejét és szimulációs idejét is megőriztük. A helyi idő ezeken a dátumokon UTC+2. A host futásidő tartalmazza az indítást, HTTP-ellenőrzést, exportot és leállítást: nem garantált válaszidő vagy a gyári folyamat időtartama.

| Futási konfiguráció | SHA-256 |
| --- | --- |
| `nominal/config.json` | `bd62ea518485835838c9a050f0c065e6ec76f949f896661cf0aa719ef16cd6c7` |
| `campaign-20261012-0715/core/config.json` | `da9946fe66434d0a5d07556d42819b43b7ed77b40022a9b40681f9b8c01a7fdb` |
| `campaign-20261012-0715/full-parking/config.json` | `28d4b4b44b2ff178f6a20686952d52c2541f72f4d3a4b255bbc87803efb67dcc` |

A fenti utak a `results/course-verification-v18/` könyvtárhoz viszonyítandók. A normál futás 63,342 s/autó megfigyelt átlagos készre-jelentéstől parkolásig tartó modellideje és 182,667 m/autó útja egyetlen funkcionális példafutás leíró adata. A hibás kohorsz időátlaga 117,142 s/autó; ebbe az injektált várakozások is beletartoznak. Ezekből nem következik általános teljesítményelőny vagy bizonytalansági intervallum.

## Elvárt és megfigyelt forgatókönyvek

A naplóazonosítók futáson belül értendők. A `journal` az alkalmazás tartós, fogadott eseménynaplója; a `receipt_id` a külön MQTT-rögzítő saját fogadási sorszáma. A két számtér eltér, ezért a hivatkozások megnevezik, melyikhez tartoznak.

| ID és eset | Elvárt viselkedés | Tényleges megfigyelés | Konkrét bizonyíték |
| --- | --- | --- | --- |
| S01: normál parkolás | Hibainjektálás nélkül hat autó engedélyt kér, külön logikai foglalást kap és beparkol. | A külön `a8d33ff8fef3` futásban 6 grant, 6 beparkolás, 0 referenciaütközés, 0 befejezetlen autó. A hat foglalás végig megmaradt. | `nominal/S01-normal-parkolas-evidence.json`: grant journal #8, #18, #27, #36, #49, #91; parkolás #147, #200, #237, #244, #249, #253. |
| S02: ismételt és közeli helyigénylés | Azonos autó/kérés nem képez második foglalást; két külön közeli kérés külön helyet kap. | A ténylegesen újrapublikált `CAR001-parking` kérés P08 helyet és ugyanazt a `6d682f18f3dc-park-1` azonosítót kapta. A mozgásmodell az ismételt engedélyt felismerte. A külön autók közeli kéréseit név szerinti koordinátorteszt vizsgálja. | Core journal #8 → #16 `parking_grant_replayed` → #17 `command_duplicate`; MQTT audit receipt #116. Külön modellteszt: `test_two_close_requests_never_share_a_space`. |
| S03: megtelt parkoló | Friss, de teljesen foglalt parkolóképből nem adható engedély; az autók várnak. | `487209a72172`: 16 foglalt hely, 0 grant, 0 mozgó autó; 1 autó a kijáratnál és 5 a pufferben. | Full-parking journal #8: `no_available_slot`, `available_count=0`; MQTT audit receipt #11; `S03-megtelt-parkolo-state.json`. |
| S04: zebra helyi megállás | Virtuális gyalogosészlelésből fékezés és tényleges megállás a zebra előtt; szabaddá válás után folytatás. | CAR001 a ZEBRA1 előtt x=39,1489 m-nél 0 m/s sebességgel állt. A jármű eleje x=41,3489 m, a zebra x=43 m. A friss minta kora 0,05 s, a vezérlés célsebessége 0. x>45 m-nél ismét 3 m/s mozgás érkezett. | Core journal #23: `pedestrian_stop`; #35: tényleges megállás, t=10,95 s; #58: zebra szabaddá válása, t=26,45 s; állapotpillanat t=11,3 s, folytatás t=29,95 s. |
| S05: érzékelőkiesés és helyreállás | Elavult helyi keretből célsebesség 0 és megállás; elavult központi parkolóadatból új engedély nincs. A foglalás nem törlődik időtúllépéssel. Új minta után folytatás. | CAR001 t=33,1 s-nél állt, eredeti szenzoróra 31,05 s, adatkor 2,05 szimulációs s. A külön stale protokollpróba hét elérhetőnek látszó hely mellett is `parking_wait` választ kapott: központi adatkor 2,1833438 host s. A régi foglalás megmaradt; t=55,85 s-nél új friss minta és mozgás érkezett. | Core journal #105: tényleges injektált hiba; #131: `stale_parking_sensor`; #133: `sensor_recovered`. A megállások journal #113–#116. MQTT stale-audit receipt #602; `S05-erzekelokieses-state.json`. |
| S06: kommunikációhiba és helyreállás | Valódi szimulátor-MQTT kapcsolatmegszakítás; helyi megállás; újrafeliratkozás és folytatás. Kiesés alatt a kijelzett távoli állapot elavult. | CAR001 t=57 s-nél 0 m/s-ra állt, `Az MQTT-kapcsolat megszakadt` okkal. A kapcsolat t=72,35 s-nél helyreállt. A megállás eredeti eseménye csak ezután érkezett be; friss mozgó kép t=73,3 s-nél. | Core journal #148: fault; #152: `mqtt_recovered`; #163: később fogadott, t=57 s-os `vehicle_stopped`. MQTT recovery receipt #664, queued-stop receipt #675. |

Az S02 megismételt kérését egy valódi, külön MQTT-kliens publikálta az adott szimulált autó aktuális kérési azonosítójával. Az S05 `COURSE_STALE_PROBE` kérés egy kifejezetten megjelölt protokollteszt: ehhez az azonosítóhoz nincs fizikai szimulált autó. Valódi MQTT-üzenetet küld az elavulttá vált, valódi szimulátorérzékelésre támaszkodó koordinátornak; a várt válasz kizárólag elutasító várakozás. Ez nem új autómozgási eredmény, és nem mesterséges szenzor- vagy telemetriaadat. A parkolóképek, a járműtelemetria és az események a meglévő szimulátorból érkeztek; SQL-beszúrással nem készült bizonyíték.

A közeli külön kérések tesztje egymás után, köztes mozgás nélkül hívja a koordinátort, és ellenőrzi a külön helyet és foglalásazonosítót. Ez a soros, egy tulajdonos által kezelt döntési állapotot igazolja. A kampány nem állít két OS-szál tökéletes egyidejűségét vagy konkurens hálózati terhelési kapacitást.

## Foglalási következetesség mint facet

A vizsgált tulajdonság: egy parkolóhelynek egy logikai foglalása lehet, és egy autó azonos vagy új kérési azonosítóval sem szerezhet második helyet. Friss, érvényes parkolóérzékelés kell az új granthez. Az idő múlása önmagában nem töröl foglalást: az elengedéshez egyező távozási azonosító és későbbi, üresnek érzékelt hely szükséges. Konfliktuszónát sem szabadít fel önmagában timeout.

| Követelmény | Új közvetlen tesztazonosító | Tényleges MQTT-kiegészítés |
| --- | --- | --- |
| Két közeli kérés nem kapja ugyanazt a helyet | `tests.test_coordinator.CoordinatorTests.test_two_close_requests_never_share_a_space` | A névleges és hibafutás minden fogadott koordinátor-pillanatképében külön helytulajdonosok és foglalásazonosítók. |
| Ismételt kérés ugyanazt az engedélyt használja | `...test_duplicate_request_reuses_grant_without_second_reservation` és `tests.test_coordinator_audit.CoordinatorAuditTests.test_selected_candidate_cost_and_replay_are_correlated_without_new_reservation` | S02 #16–#17; nem indul újra a mozgás, nincs második hely. |
| Új request_id ugyanahhoz az autóhoz sem ad új helyet | `...test_new_request_id_cannot_reserve_twice_for_same_car` | Fogadott koordinátor-frame invariánsellenőrzés. |
| Hiányzó, régi, rossz sorrendű vagy idegen futású szenzoradat nem frissít és nem engedélyez | `...test_no_parking_grant_before_first_sensor_sample`, `...test_reordered_sensor_sample_cannot_overwrite_or_refresh_state`, `...test_stale_payload_and_foreign_run_do_not_update_sensor` és `tests.test_coordinator_audit.CoordinatorAuditTests.test_rejected_frame_reasons_are_visible_and_do_not_refresh_physical_evidence` | S05 #131: hét jelölt mellett is elutasítás; a hat tényleges grant központi adatkorának ellenőrzése mindkét hat autós futásban. |
| Timeout nem töröl logikai foglalást | `...test_timeout_never_releases_logical_reservation` és `...test_zone_timeout_does_not_free_occupied_area` | A hibafutás 366 fogadott koordinátor-frame-jében hat tartós foglalás; a szenzor- és kapcsolatfrissesség lejárata sem törölt tulajdonost. A teljes 100 s-os mesterséges időléptetést a külön unit teszt vizsgálja. |
| Elengedéshez egyező azonosító és későbbi fizikai igazolás szükséges | `...test_departure_requires_matching_id_and_later_empty_sensor`, `...test_late_duplicate_request_cannot_replay_closed_reservation`, `...test_zone_exit_needs_matching_grant_and_whole_vehicle_outside` és `tests.test_coordinator_audit.CoordinatorAuditTests.test_parking_release_records_later_empty_frame_certificate` | A három rövid kampány parkolási tartózkodása meghaladja a vizsgált időt; távozás nem történt. Az elengedési feltétel e kampányban kizárólag külön unit bizonyíték. |

A `...` a táblázatban a `tests.test_coordinator.CoordinatorTests.` előtag rövidítése. A teljes tizennégy pontos ID és a reprodukciós parancs a kampány `summary.json` fájljában, a tényleges futtatás kimenete a `targeted-tests.txt` fájlban van: `Ran 14 tests ... OK`. Ez a réteg közvetlen Python koordinátorteszt, benne a teszt adja a modellórát és a payloadokat; külön kell kezelni a tényleges brokeres bizonyítéktól.

A fogadott frame-ekre a rögzítő azt is vizsgálta, hogy egy autó vagy foglalásazonosító ne forduljon elő több parkolóhelyen, és távozás nélküli futásban ne változzon meg korábban megfigyelt foglalás. Megfigyelt invariánssértés: 0. Ez a rögzített frame-ekre vonatkozó ellenőrzés; nem formális bizonyítás minden lehetséges állapotátmenetre.

## Visszakereshető naplórészlet

Az alábbi kivonat a core `events.json` eredeti mezőiből készült. A #152/#163 fogadási sorrend szándékosan különbözik a szimulációs időtől: a hálózati kiesés alatt keletkező esemény később érkezik.

```json
{"id":8,"sim_time":0.05,"event_type":"parking_granted","car_id":"CAR001","slot_id":"P08","reservation_id":"6d682f18f3dc-park-1"}
{"id":16,"sim_time":6.7,"event_type":"parking_grant_replayed","reason":"idempotent_request","reservation_id":"6d682f18f3dc-park-1"}
{"id":17,"sim_time":6.95,"event_type":"command_duplicate","reason":"idempotent_grant"}
{"id":35,"sim_time":10.95,"event_type":"vehicle_stopped","reason":"Gyalogos: ZEBRA1","speed_m_s":0.0,"position_m":[39.148925,58.0]}
{"id":131,"sim_time":47.95,"event_type":"parking_wait","car_id":"COURSE_STALE_PROBE","reason":"stale_parking_sensor","available_count":7,"sensor_age_s":2.1833438}
{"id":152,"sim_time":72.35,"event_type":"mqtt_recovered","received_utc":"2026-10-12T07:15:12.502Z"}
{"id":163,"sim_time":57.0,"event_type":"vehicle_stopped","reason":"Az MQTT-kapcsolat megszakadt","speed_m_s":0.0,"received_utc":"2026-10-12T07:15:12.519Z"}
```

A kivonat a beágyazott `details` mezők közül a relevánsakat a sorba emeli; a teljes, eredeti beágyazás és minden azonosító a változatlan `events.json`, `mqtt-correlation.jsonl` és `run-export.zip` állományokban maradt meg. A tizenhat helyből teljesen foglalt külön futás journal #8 rekordjának oka `no_available_slot`, `available_count=0`, `sensor_age_s=0.0015156`: ez friss adatból hozott kapacitáshiány-döntés, nem érzékelőkiesés.

## Forrásegyezés és archívum

Mindkét friss jelentés a futtatás elején és végén SHA-256 lenyomatot készített a `factory_yard` Python-forrásairól, az alapkonfigurációról, az ellenőrző programról és a célzott tesztfájlokról. A lenyomatok a futás közben nem változtak. A névleges kiegészítés saját ellenőrző programját is hash-eli. A teljes forráslista a jelentésekben szerepel, az alábbiak a fő megvalósítási belépőpontok:

| Forrás | SHA-256 |
| --- | --- |
| `factory_yard/model.py` | `efcbdeae507b9ef15782a978f4b261fc5f0e825be2a11b5ac888bbdb34d9ab12` |
| `factory_yard/coordinator.py` | `17e737fbaf5828fffe2d69ac8984914e5323f7e30a45fb323d80d7de83ea5cbc` |
| `factory_yard/server.py` | `a51a6252bf3c0ecc9706c5fae5a95f0980523f82affe02bfc9ac9f00b170a616` |
| `experiments/course_verification.py` | `0f562aced6dd1835ffd79aeb10227f06024a148f4dd524917f59d3546fd48570` |

Minden tényleges futáshoz mentettük a futási konfigurációt, a HTTP-kérések idejét és a kezelői műveletek válaszazonosítóját, a teljes lapozott eseménynaplót, az egyes esetek állapotát és naplórészleteit, a worker logokat, a teljes futás HTTP-n letöltött ZIP-exportját és annak metaadatait. A ZIP CRC-je megfelelő, a belső run_id és config a tényleges futással egyezik. A fő jelentések valamennyi futási műtermékhez SHA-256 lenyomatot közölnek.

Kiegészítő, már meglévő bizonyíték a `results/operations-check-v18.json`: 18 tényleges MQTT/HTTP ellenőrzés, `4bb55df8d688` futás, 2026-10-11 20:44:01.952856 UTC. A friss kampány a benne tárolt valamennyi forráslenyomatot összevetette a jelenlegi fájlokkal: eltérés nincs, `accepted_as_current=true`. Ez forrásegyezéssel elfogadott korábbi kiegészítő kampány, nem újrafuttatott 18 ellenőrzés. Térképe két autós, nagyobb győri modell, ezért a témalapi hat autós esettáblázat helyett az incidenséletciklus, nyugtázás, archiválás és HTTP-export külön bizonyítéka.

Az első, sandboxban végzett indítást a helyi TCP-hozzáférés `WinError 10013` tiltása állította meg. Ezt a `results/course-verification-v18/failure.json` őrzi meg; a sikeres kampányok külön könyvtárba kerültek, helyi TCP-t engedő futtatás mellett. A sikertelen indítás nem része a PASS eredménynek. A broker logjában a rövid TCP portellenőrzésből származó `No more data` figyelmeztetés is megmaradt; a kapcsolatot a teszt portfelderítés után MQTT-kézfogás nélkül zárja le. A tényleges MQTT-kérelmek, grant-ek és nyugták ettől elkülönülten ellenőrzöttek.

## Fogadott teljes snapshotok videóhoz

| Forrásfájl a bizonyítékgyökér alatt | Snapshotok | Külön koordinátor-frame-ek | Fogadott szimulációs időtartomány |
| --- | ---: | ---: | --- |
| `nominal/full-visual-frames.jsonl` | 289 | 276 | 0,05–142,85 s |
| `campaign-20261012-0715/core/full-visual-frames.jsonl` | 369 | 366 | 0,05–196,95 s |
| `campaign-20261012-0715/full-parking/full-visual-frames.jsonl` | 64 | 64 | 0,05–51,05 s |

Minden sor valódi MQTT-kliens által fogadott teljes szimulátor-pillanatképet tartalmaz: `frame_index`, `receipt_id`, fogadási UTC és monotón host óra, `sim_time`, `snapshot`, valamint a legutolsó külön fogadott `central_overlay` a saját fogadási órájával. A `snapshot` magában foglalja a térképet, autókat, pozíciókat, állapotot, sebességet, érzékelést, tényleges vezérlési döntést, gyalogosokat, fizikai parkolóállapotot és mutatókat. A central overlay a központi logikai foglalásnézet.

Képkocka nem interpolált és nem pótolja a kiesést. A 16 szimulációs másodperces MQTT-hiba alatt nincs új szimulátor-snapshot. A grafikus rekonstrukció a legutolsó kapott képet tarthatja, de felirattal jelezze: **„Kommunikációs kiesés – utolsó fogadott kép; a megállási esemény később érkezik.”** A central overlay és a szimulátorállapot eltérő fogadási időpontú, nem atomikus közös mérés. A mintaidő, fogadási idő és vezérlési döntési idő külön megőrzendő.

A kész videó megnevezése: **„Valódi MQTT-n fogadott szimulációs állapotok rekonstrukciója”**. A frame-archívum nem felhasználói felületi képernyőfelvétel, nem fizikai kamerafelvétel, és nem teszi valós ipari rendszer igazolásává a modellt. A videó vágási ideje eltérhet a szimulációs és a host időtől; a képen a saját eredeti `sim_time` maradjon látható.

## Magyar bemutató-forgatókönyv, 4 perc 40 másodperc

| Videóidő | Mi látszik | Rövid narráció és bizonyító érték |
| --- | --- | --- |
| 0:00–0:25 | Témalapi térkép, 6 autós kohorsz, 16 hely, 2 zebra; a rendszerhatár és a névleges run_id. | „A gyártósori kijárattól a parkolóig modellezett folyamatot külön szimulátor és koordinátor vezérli. Az adatút valódi helyi TCP/MQTT, a fizikai folyamat és az érzékelők virtuálisak.” |
| 0:25–1:10 | A `nominal` futás fogadott képei, P08 grant részlete, autók mozgása és a 6/6 beparkolás. | „Kérés → központi engedély → helyi mozgás → parkolás. Ez a külön normál futás nem tartalmaz hibainjektálást. Hat külön foglalást és nulla modellezett ütközést figyeltünk meg.” Mutasd a journal #8 és a végállapot hivatkozását. |
| 1:10–1:50 | A core ismételt kérésének #8/#16/#17 naplórészlete; ugyanaz a P08 és reservation_id. Mellette a közeli két kérés tesztjének neve és PASS sora. | „A megismételt MQTT-kérés ugyanazt a foglalást kapja, az autó nem indul újra. Két külön, közvetlenül egymás után érkező kérés külön helyét külön koordinátorteszt ellenőrzi; ez nem konkurens hálózati terhelésmérés.” |
| 1:50–2:25 | A `full-parking` futás: minden hely foglalt, a kijárati autó áll, a számláló 1 aktív + 5 puffer, elutasítás `no_available_slot`. | „A teljes parkoló friss érzékelése mellett nincs új engedély. A további autók a termelési pufferben maradnak; a rendszer nem rajzol be hat mozgó autót szabad hely nélkül.” |
| 2:25–3:00 | Core t≈7–30 s: CAR001 fékez, a gyalogos átkel; sebesség 0, x=39,1489 m; majd újra mozgás. | „A zebra miatti megállást a helyi virtuális észlelés váltja ki. A nulla sebesség és a tényleges megállási esemény együtt igazolható, nem csupán az állapotnév.” |
| 3:00–3:45 | Core t≈31–56 s: a mintaóra 31,05 s-nál marad, adatkor nő, cél- és tényleges sebesség 0; stale-audit #131, később friss minta. | „Régi minta nem válik új méréssé. A külön MQTT protokollpróba hét látszólag elérhető hely mellett is elutasítást kap, mert a parkolóérzékelés elavult. Az addigi foglalás közben nem törlődik.” Jelezd, hogy a probe-hoz nincs mozgó autó. |
| 3:45–4:25 | Core t≈56–73 s: kommunikációs hiba, megjelölt álló utolsó kép, nincs hamis új frame; #152 és #163 fogadási idő mellett; friss mozgás visszatér. | „Kiesés alatt az autó helyben fékez, de erről nem érkezhet friss távoli kép. Az eredeti t=57 s-os megállási esemény a t=72,35 s-os visszakapcsolódás után jut a naplóba. A fogadási sorrend ezért eltér a modellidőtől.” |
| 4:25–4:40 | Összesítő: 6 eset PASS, 14 célzott teszt, 3 valódi MQTT-futás, 0 megfigyelt foglalási invariánssértés, jelentés és forráslenyomat. | „A vizsgált véges modell és adatút eredménye reprodukálható. Az idealizált útvonalkövetés, a kooperatív gyalogos és a virtuális érzékelés korlátai megmaradnak; ez nem általános ipari biztonsági tanúsítás.” |

A videó nem készít statisztikai következtetést egyetlen normál futásból. A parkolókiosztási stratégiák külön páros mérési kampányának eredménye legfeljebb a záró jelentésben, saját forrás- és konfigurációazonosítóval kapcsolható ehhez a funkcionális bizonyítékhoz.

## Reprodukció

A meglévő bizonyítékot a programok nem írják felül. Új teljes futáshoz a `projekt` munkakönyvtárból új mappát kell választani. Az alábbi PowerShell-sorok ugyanazokat az ellenőrző programokat használják; az új mag változatlanul 123, a hálózati portok és a run_id-k futásonként eltérnek:

```powershell
$courseOutput = "results/course-verification-repro-" + (Get-Date -Format "yyyyMMdd-HHmmss")
.\.venv\Scripts\python.exe -m experiments.course_verification --output "$courseOutput/campaign"
Copy-Item -LiteralPath "results/course-verification-v18/nominal-proof.py" -Destination "$courseOutput/nominal-proof.py"
.\.venv\Scripts\python.exe "$courseOutput/nominal-proof.py"
```

A szkript loopback TCP-t engedő környezetben futtatható; a korlátozott sandbox ugyanezt a helyi kapcsolatot is blokkolhatja. A futtatások valós broker/worker folyamatokat indítanak és végül leállítanak. A célzott tesztek a fő kampány részeként automatikusan futnak; önállóan is futtathatók a `summary.json` teljes `targeted_tests.command` parancsával.

Kézi bemutatóhoz külön fájl készült: `config/course-demo.json`, SHA-256 `2a2046b207029c8e400d8942103ada476760a16c3beea705496e732b57c4b6b7`. Ez a témalapi geometriát és a kontrollált kampány termelési beállításait tartalmazza, MQTT-portja 19084. Így a `factory_yard run` alapértelmezésben végzett nagyobb-térkép fordítása nem váltja fel a beadandó kis referenciáját:

```powershell
.\.venv\Scripts\python.exe -m factory_yard run --config config/course-demo.json --seed 123 --strategy nearest --speed 4 --http-port 8876
```

A kézi bemutató címe `http://127.0.0.1:8876`. A mért kampány a mentett tényleges konfigurációkhoz tartozik, nem ehhez a későbbi kézi futáshoz. A bemutatóconfig rögzített portját foglaltság esetén módosítani kell; a bizonyítékszkript automatikusan szabad portot választ. A kampányok és dokumentumok helyben készültek; publikálás nem történt.
