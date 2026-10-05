"""Multilingual keyword tables for deterministic offer parsing (data only).

Every entry is a lowercase regex fragment matched with word boundaries handled
by the caller. Languages covered: en, de, fr, it, es, nl, pt, sv, pl.
"""

# --- Section headings -------------------------------------------------------
# A heading line is classified by the first class whose fragment it contains.
# Order matters: more specific classes first ("about the role" is not "about us").

SECTION_HEADINGS: dict[str, list[str]] = {
    "responsibilities": [
        r"responsibilit", r"what you'?ll do", r"what you will do", r"your role", r"the role",
        r"about the role", r"about the job", r"about the position", r"role overview", r"job description",
        r"your (?:mission|tasks|impact|day)", r"key (?:tasks|duties)", r"duties", r"day[- ]to[- ]day",
        r"what you'?ll be doing", r"in this role", r"the opportunity", r"the position", r"tasks",
        # de
        r"aufgaben", r"deine rolle", r"ihre rolle", r"das erwartet dich", r"was dich erwartet",
        r"was sie erwartet", r"tätigkeiten", r"stellenbeschreibung",
        # fr
        r"^missions?$", r"^la mission$", r"vos missions", r"tes missions", r"le poste", r"descriptif du poste",
        r"responsabilités", r"votre rôle", r"ton rôle",
        # it
        r"mansioni", r"responsabilità", r"il ruolo", r"attività", r"cosa farai", r"compiti",
        # es
        r"funciones", r"responsabilidades", r"tus tareas", r"el puesto", r"qué harás", r"tu rol",
        # nl
        r"taken", r"verantwoordelijkheden", r"wat ga je doen", r"de functie", r"jouw rol",
        # pt
        r"responsabilidades", r"atividades", r"o que vais fazer", r"o que você vai fazer",
        # sv
        r"arbetsuppgifter", r"ansvarsområden", r"rollen",
        # pl
        r"obowiązki", r"zakres obowiązków", r"twoje zadania",
    ],
    "requirements": [
        r"requirement", r"qualifications?", r"what you'?ll bring", r"what you bring", r"what we'?re looking for",
        r"who you are", r"about you", r"your profile", r"profile", r"skills", r"must[- ]haves?",
        r"nice[- ]to[- ]haves?", r"you have", r"you should have", r"experience", r"what you need",
        r"ideal candidate", r"preferred", r"bonus points", r"we'?re looking for",
        # de
        r"anforderungen", r"dein profil", r"ihr profil", r"qualifikation", r"das bringst du mit",
        r"was du mitbringst", r"was sie mitbringen", r"voraussetzungen", r"kenntnisse",
        # fr
        r"profil recherché", r"votre profil", r"ton profil", r"compétences", r"qualifications requises",
        r"prérequis", r"ce que nous recherchons",
        # it
        r"requisiti", r"il tuo profilo", r"profilo", r"competenze", r"cosa cerchiamo",
        # es
        r"requisitos", r"tu perfil", r"perfil", r"lo que buscamos", r"qué buscamos",
        # nl
        r"functie-?eisen", r"wat vragen wij", r"wat breng je mee", r"jouw profiel", r"vereisten",
        # pt
        r"requisitos", r"o teu perfil", r"seu perfil", r"qualificações",
        # sv
        r"kvalifikationer", r"krav", r"din profil", r"vem är du",
        # pl
        r"wymagania", r"oczekujemy", r"twój profil", r"kwalifikacje",
    ],
    "benefits": [
        r"benefits?", r"perks", r"what we offer", r"we offer", r"what'?s in it for you", r"why join",
        r"why work", r"compensation", r"our offer", r"rewards", r"what you get",
        # de
        r"wir bieten", r"was wir bieten", r"unser angebot", r"deine vorteile", r"ihre vorteile", r"benefits",
        # fr
        r"ce que nous offrons", r"nous offrons", r"avantages", r"pourquoi nous rejoindre", r"on vous offre",
        # it
        r"cosa offriamo", r"offriamo", r"benefit", r"vantaggi",
        # es
        r"qué ofrecemos", r"ofrecemos", r"beneficios",
        # nl
        r"wat bieden wij", r"wij bieden", r"arbeidsvoorwaarden",
        # pt
        r"oferecemos", r"benefícios",
        # sv
        r"vi erbjuder", r"förmåner",
        # pl
        r"oferujemy", r"benefity",
    ],
    "company": [
        r"about us", r"about the company", r"who we are", r"our company", r"company overview",
        r"our story", r"our mission", r"our team", r"the team", r"about the team", r"our culture",
        r"about [a-z0-9&.\- ]{1,30}$",
        # de
        r"über uns", r"wer wir sind", r"unternehmen", r"das unternehmen", r"unser team",
        # fr
        r"qui sommes[- ]nous", r"à propos", r"l'entreprise", r"notre entreprise", r"la société",
        # it
        r"chi siamo", r"l'azienda", r"azienda",
        # es
        r"quiénes somos", r"sobre nosotros", r"la empresa",
        # nl
        r"over ons", r"wie zijn wij", r"het bedrijf",
        # pt
        r"quem somos", r"sobre nós", r"a empresa",
        # sv
        r"om oss", r"vilka vi är",
        # pl
        r"o nas", r"kim jesteśmy",
    ],
    "apply": [
        r"how to apply", r"application process", r"next steps", r"interview process", r"apply now",
        r"our process", r"hiring process", r"recruitment process",
        # de
        r"bewerbung", r"bewirb dich", r"kontakt", r"ansprechpartner", r"so bewirbst du dich",
        # fr
        r"candidature", r"processus de recrutement", r"postuler",
        # it
        r"candidatura", r"come candidarsi", r"processo di selezione",
        # es
        r"cómo aplicar", r"proceso de selección", r"inscríbete",
        # nl
        r"solliciteren", r"sollicitatie", r"interesse",
        # pt
        r"candidatura", r"processo de recrutamento",
        # sv
        r"ansökan", r"sök tjänsten",
        # pl
        r"aplikuj", r"rekrutacja", r"proces rekrutacji",
    ],
    "legal": [
        r"equal opportunit", r"diversity", r"inclusion", r"eeo", r"privacy", r"data protection",
        r"disclaimer", r"legal", r"accommodation",
        r"datenschutz", r"chancengleichheit", r"diversité", r"protection des données",
        r"privacy policy", r"informativa", r"protección de datos", r"privacybeleid",
        r"ochrona danych", r"rodo", r"klauzula",
    ],
}

# Sections whose content is dropped from the kept text.
DROPPED_SECTIONS = {"company", "benefits", "apply", "legal"}

# --- Boilerplate paragraphs (dropped wherever they appear) ------------------

BOILERPLATE_PHRASES: list[str] = [
    r"equal opportunity employer", r"equal employment opportunity", r"without regard to (?:race|age|sex)",
    r"regardless of (?:race|gender|age|religion)", r"race, colou?r, religion", r"protected veteran",
    r"reasonable accommodation", r"we are committed to (?:diversity|creating an inclusive)",
    r"we (?:celebrate|value|embrace) diversity", r"all qualified applicants",
    r"privacy (?:notice|policy)", r"personal data", r"gdpr", r"by applying,? you",
    r"recruitment agencies", r"unsolicited (?:cv|resume)",
    # de
    r"unabhängig von (?:geschlecht|alter|herkunft)", r"schwerbehinderte", r"datenschutzerklärung",
    r"personenbezogene(?:n)? daten", r"(?:m|w|d)/(?:w|m|d)/(?:d|w|m)\)?\s*$",
    # fr
    r"sans distinction", r"handicap", r"données personnelles", r"égalité des chances",
    # it
    r"d\.?\s?lgs", r"pari opportunità", r"dati personali", r"ai sensi del",
    # es
    r"igualdad de oportunidades", r"datos personales",
    # nl
    r"gelijke kansen", r"persoonsgegevens", r"acquisitie naar aanleiding",
    # pl
    r"przetwarzanie moich danych", r"dane osobowe", r"wyrażam zgodę",
]

# --- Seniority --------------------------------------------------------------
# Mirrors backend/backend/search/query_parsing.py _ENUM_RULES (seniority rows), plus
# multilingual title words. First match wins, checked in this order.

SENIORITY_TITLE: list[tuple[str, str]] = [
    (r"\bdirector\b|\bhead\b|\bdirect(?:eur|rice|ora)\b|\bleiter(?:in)?\b|\bresponsabile\b", "director"),
    (r"\bprincipal\b", "principal"),
    (r"\bstaff\b", "staff"),
    (r"\blead\b|\btech lead\b|\bteam lead\b|\bteamleiter|\blead(?:er)?\b|\bchef de projet\b|\bresponsable\b", "lead"),
    (r"\b(?:engineer|developer|scientist|analyst|sde)\s+(?:iii|iv|3)\b|\bsenior\b|\bsr\.?\b|\bconfirmé(?:e)?\b|\bexpérimenté(?:e)?\b", "senior"),
    (r"\b(?:engineer|developer|scientist|analyst|sde)\s+(?:ii|2)\b|\bmid(?:[- ]?level)?\b|\bintermediate\b|\bmedior\b|\bregular\b", "mid"),
    (r"\bjunior\b|\bjr\.?\b|\bentry[- ]?level\b|\bgraduate\b|\bdébutant(?:e)?\b|\bneolaureat|\btrainee\b|\bberufseinsteiger", "junior"),
    (r"\bintern(?:ship)?\b|\bpraktik(?:ant|um)|\bstagiaire\b|\btirocin(?:ante|io)\b|\bwerkstudent"
     r"|\bstage\b(?!\s+(?:of|in)\b)|\balternan(?:ce|t)|\bapprenti|\bapprentice|\bauszubildende|\bazubi\b", "junior"),
]

# Years-of-experience phrases; the captured group is the minimum years
# (≤2 → junior, 3-4 → mid, 5+ → senior).
YEARS_EXPERIENCE: list[str] = [
    r"(\d{1,2})\s*\+?\s*(?:-|–|to)?\s*\d{0,2}\s*\+?\s*(?:years?|yrs?)(?:'|’)?\s*(?:of\s+)?(?:[\w-]+\s+){0,4}?(?:experience|exp\b)",
    r"(?:minimum|at least|min\.?)\s+(?:of\s+)?(\d{1,2})\s*\+?\s*(?:years?|yrs?)",
    r"(\d{1,2})\s*\+?\s*(?:-|–|bis)?\s*\d{0,2}\s*jahre?\w*\s+(?:\w+\s+){0,3}?(?:berufserfahrung|erfahrung)",
    r"(?:mindestens|min\.)\s+(\d{1,2})\s*jahre?",
    r"(\d{1,2})\s*\+?\s*(?:-|–|à)?\s*\d{0,2}\s*(?:ans?|années?)\s+(?:d'|de\s+)?(?:\w+\s+){0,2}?expérience",
    r"(\d{1,2})\s*(?:ans?|années?)\s+minimum\b",
    r"(\d{1,2})\s*\+?\s*(?:-|–|e|a)?\s*\d{0,2}\s*ann[io]\s+(?:di\s+)?(?:\w+\s+){0,2}?esperienza",
    r"(\d{1,2})\s*\+?\s*(?:-|–|y|a)?\s*\d{0,2}\s*años\s+(?:de\s+)?(?:\w+\s+){0,2}?experiencia",
    r"(\d{1,2})\s*\+?\s*(?:-|–)?\s*\d{0,2}\s*jaar\s+(?:\w+\s+){0,2}?(?:werk)?ervaring",
    r"(\d{1,2})\s*\+?\s*(?:-|–|a)?\s*\d{0,2}\s*anos\s+(?:de\s+)?(?:\w+\s+){0,2}?experiência",
    r"(\d{1,2})\s*\+?\s*(?:-|–)?\s*\d{0,2}\s*års?\s+(?:\w+\s+){0,2}?(?:arbets)?(?:livs)?erfarenhet",
    r"(\d{1,2})\s*\+?\s*(?:lata|lat)\s+(?:\w+\s+){0,2}?doświadczeni",
    # "Esperienza di almeno 2 anni", "Experiencia mínima de 2 años", "expérience de 3 ans minimum", "mínima de 6 anos"
    r"(?:experience|expérience|esperienza|experiencia|experiência|erfahrung|ervaring|erfarenhet|doświadczeni\w*)"
    r"\W+(?:\w+\W+){0,3}?(\d{1,2})\s*\+?\s*(?:years?|ans?|anni|años|anos|jahre?n?|jaar|års?|lat[a]?)\b",
    r"(?:almeno|al menos|minst|mínim[oa](?: de)?|min\.|minimaal|mindestens|au moins|ao menos|pelo menos|co najmniej)\s+(\d{1,2})\s*\+?\s*(?:ann[io]|años|anos|års?|ans?|années?|jahre?n?|jaar|lat[a]?)\b",
    # Indeed form "Esperienza: cameriere ai piani: 2 anni (Obbligatorio)"
    r"(\d{1,2})\s*(?:anni|años|anos|years?|ans?)\s*\((?:obbligatorio|obligatorio|preferibile|deseable|required|preferred|obligatoire)\)",
]

# --- Employment type --------------------------------------------------------
# Checked in order; first hit wins. Default when nothing matches: full-time.

EMPLOYMENT_TYPE: list[tuple[str, str]] = [
    (r"\binternships?\b|\bintern\b|\bpraktikum\b|\bpraktikant|\btirocinio\b|\bstagiaire\b|\bprácticas\b|\bbecari[oa]\b|\bstagiair\b|\bstaż\b|\bpraktyk|\balternan(?:ce|t)|\bapprenti|\bapprenticeship\b|\bausbildung\b|\bauszubildende", "internship"),
    (r"\bstage\b(?!\s+(?:of|in)\b)", "stage"),
    (r"\bwerkstudent|\bworking student\b|\bstudentische hilfskraft", "part-time"),
    (r"\bfreelance(?:r)?\b|\bfreiberuf|\bfreie mitarbeit|\bindépendant\b|\bfreelancer\b|\bzzp\b|\bautónomo\b", "freelance"),
    (r"\bcontract(?:or)?\b(?!\s+(?:management|manager|law|negotiation))|\bfixed[- ]term\b|\btemporary\b|\bbefristet|\bcdd\b|\btempo determinato\b|\bcontrato temporal\b|\binterim\b|\bb2b\b", "contract"),
    (r"\bpart[- ]?time\b|\bteilzeit\b|\btemps partiel\b|\btempo parziale\b|\bpart-?time\b|\bmedia jornada\b|\bjornada parcial\b|\bdeeltijd\b|\bdeltid\b|\bczęść etatu\b", "part-time"),
    (r"\bfull[- ]?time\b|\bvollzeit\b|\btemps plein\b|\btempo pieno\b|\bjornada completa\b|\bvoltijd\b|\bheltid\b|\bpełny etat\b|\bcdi\b|\bunbefristet|\btempo indeterminato\b|\bpermanent\b", "full-time"),
]

# Body text is noisier ("early-stage", "contract type: permanent", "our internship
# programme"), so only explicit phrases count there, and full-time wins over
# part-time when both appear (as the old LLM prompt did).
EMPLOYMENT_TYPE_BODY: list[tuple[str, str]] = [
    (r"\bwerkstudent|\bworking student\b", "part-time"),
    (r"\bfixed[- ]term\b|\bcontract (?:role|position|basis|assignment)\b|\b(?:daily|day) rate\b|\bhourly contract\b|\b(?:kontrakt|umowa) b2b\b|\bdetachering\b|\buitzendovereenkomst\b|\bmission longue durée\b|\b\d{1,2}[- ]months? contract\b|\bproject duration\b|\bper day\b|\binside ir35\b|\boutside ir35\b"
     r"|\bb2b (?:contract|cooperation)\b|\bbefristet(?:e[rn]?)?\b(?! *unbefristet)|\bcdd\b|\btempo determinato\b|\bcontrato temporal\b|\btemporary (?:role|position|contract)\b", "contract"),
    (r"\bfreelance (?:role|position|contract|project|basis)\b|\bmission freelance\b|\ben freelance\b|\bfreiberuflich\b|\bfreie mitarbeit\b", "freelance"),
    (r"\bcontrat d'apprentissage\b|\ben alternance\b|\bcontrat de professionnalisation\b|\bthis internship\b|\binternship (?:position|role|opportunity)\b|\bpflichtpraktikum\b|\bformativo/prácticas\b|\bprácticas (?:remuneradas|profesionales|curriculares|extracurriculares)\b|\bstage de fin d'études\b|\bstage de \d", "internship"),
    (r"\bfull[- ]?time\b|\bvollzeit\b|\btemps plein\b|\btempo pieno\b|\bjornada completa\b|\bvoltijd\b|\bheltid\b|\bpełny etat\b|\bcdi\b|\bunbefristet|\btempo indeterminato\b|\bpermanent\b", "full-time"),
    (r"\bpart[- ]?time\b|\bteilzeit\b|\btemps partiel\b|\btempo parziale\b|\bmedia jornada\b|\bjornada parcial\b|\bdeeltijd\b|\bdeltid\b|\bczęść etatu\b", "part-time"),
]

# --- Location type ----------------------------------------------------------

LOCATION_HYBRID = (
    r"\bhybrid\b|\bhybrid(?:e[nrs]?|es)\s+(?:arbeit|work|model)\w*|\b(?:travail|mode|format|rythme|organisation|modalité|poste)\s+hybride\b|\bhybride werk\w*|\bhybride\s*$"
    r"|\bh[ií]brid[oa]\b|\bflessibilità (?:in )?remot\w*|\bremote-anteil|\bhome working\b|\bpossibility (?:of|to) work(?:ing)? (?:from home|remotely)\b|\bibrid[oa]\b|\bhybryd"
    r"|\bmix of (?:remote|office|both)\b|\bpartially remote\b|\bpartly remote\b|\bremote[- ]friendly\b"
    r"|\bsome remote\b|\bremote flexibility\b|\bflexibility to work (?:from home|remotely)\b"
    r"|\b(?:home|remote(?:ly)?),? (?:or|and) (?:the |our |an )?office\b|\boffice,? (?:or|and) (?:from )?(?:home|remote(?:ly)?)\b"
    r"|\bmobile[sn]? arbeit|\bmobiles? arbeiten|\bhome[- ]?office\b|\btravail hybride\b|\blavoro ibrido\b|\bsmart working\b"
    r"|\bjours? de télétravail\b|\btélétravail (?:partiel|possible|\d)|\bpossibilité de (?:faire du )?télétravail"
    r"|\bwork(?:ing)? from home (?:up to |for )?(?:\d|one|two|three|four) days?|\b(?:\d|one|two|three|four)[- ]days? (?:of )?(?:wfh|work(?:ing)? from home|home ?working)"
    r"|\bthuiswerkmogelijkheid \(maxim|\bremote[- ]first\b[^.\n]{0,60}\b(?:office|collaboration|team) days?\b"
    # "3 days a week in the office", "Présence sur site : 3 jours par semaine", "2 Tage Homeoffice"
    r"|\b(?:[1-4]|one|two|three|four|zwei|drei|vier|deux|trois|quatre|due|tre|quattro|dos|tres|cuatro|twee|drie)"
    r"\s*(?:\([1-4]\)\s*)?(?:days?|tage[n]?|jours?|giorni|días|dias|dagen|dagar)\b"
    r"(?!\s*(?:of\s+)?(?:paid|annual|holiday|vacation|leave|urlaub|congés|ferie|pto|off|per year|/year|a year|im jahr|par an))"
    r"[^.\n]{0,40}"
    r"(?:office|büro|buero|site|bureau|kantoor|ufficio|oficina|remote|home|hause|casa|domicile|télétravail|mobil|distans|thuis)"
    r"|\b(?:office|büro|on[- ]site|sur site|bureau|kantoor|ufficio|oficina)\b[^.\n]{0,40}"
    r"\b(?:[1-4]|one|two|three|four|deux|trois|quatre|zwei|drei|vier)\s*(?:days?|tage|jours?|giorni|días|dagen)\b"
)
LOCATION_REMOTE = (
    r"\bremote\b|\bfully remote\b|\bwork from home\b|\bwfh\b|\b100\s?% (?:remote|télétravail|homeoffice)\b"
    r"|\bfull[- ]remote\b|\btélétravail (?:complet|total)\b|\bfull télétravail\b|\bà distance\b|\bda remoto\b|\bremoto\b"
    r"|\bteletrabajo\b|\bremot[ao]\b|\bthuiswerk|\bzdaln|\banywhere in (?:europe|the world)\b|\b(?:100\s?% )?distributed (?:team|company|setting|setup)\b|\bdistansarbete\b|\bvollständig remote\b"
)
# Explicit "no remote" statements beat any remote/hybrid keyword in the body.
LOCATION_NOT_REMOTE = (
    r"\b100\s?% presencial\b|\b(?:do|does|will) not (?:offer|support|allow) remote\b|\bnot (?:a )?remote (?:role|position)\b"
    r"|\bno remote\b|\bkein(?:e)? (?:home ?office|remote)\b|\bpas de télétravail\b|\bnessuna possibilità di smart working\b"
)
LOCATION_ONSITE = (
    r"\bon[- ]?site\b|\bin[- ]office\b|\bvor ort\b|\bpräsenz|\bsur site\b|\bpresenziale\b|\bin sede\b"
    r"|\bpresencial\b|\bop locatie\b|\bstacjonarn|\b5 days (?:a|per) week (?:in|at) (?:the )?office\b"
)

# Title/location markers that state the arrangement explicitly (stronger than body text).
REMOTE_IN_LOCATION = r"\bremote\b|\banywhere\b|\bwork from home\b|\bhomeoffice\b|\btélétravail\b"

# --- Languages --------------------------------------------------------------
# canonical english name -> name variants in the supported languages.

LANGUAGE_NAMES: dict[str, list[str]] = {
    "english": ["english", "englisch", "anglais", "inglese", "inglés", "ingles", "engels", "inglês", "engelska", "angielski"],
    "german": ["german", "deutsch", "allemand", "tedesco", "alemán", "aleman", "duits", "alemão", "tyska", "niemiecki"],
    "french": ["french", "französisch", "français", "francais", "francese", "francés", "frances", "frans", "francês", "franska", "francuski"],
    "italian": ["italian", "italienisch", "italien", "italiano", "italiaans", "italienska", "włoski"],
    "spanish": ["spanish", "spanisch", "espagnol", "spagnolo", "español", "espanol", "spaans", "espanhol", "spanska", "hiszpański"],
    "dutch": ["dutch", "niederländisch", "néerlandais", "olandese", "neerlandés", "nederlands", "holandês", "nederländska", "niderlandzki", "flemish"],
    "portuguese": ["portuguese", "portugiesisch", "portugais", "portoghese", "portugués", "portugees", "português", "portugisiska", "portugalski"],
    "swedish": ["swedish", "schwedisch", "suédois", "svedese", "sueco", "zweeds", "svenska", "szwedzki"],
    "polish": ["polish", "polnisch", "polonais", "polacco", "polaco", "pools", "polonês", "polska", "polski"],
    "danish": ["danish", "dänisch", "danois", "danese", "danés", "deens", "dinamarquês", "danska", "duński"],
    "norwegian": ["norwegian", "norwegisch", "norvégien", "norvegese", "noruego", "noors", "norueguês", "norska", "norweski"],
    "finnish": ["finnish", "finnisch", "finnois", "finlandese", "finlandés", "fins", "finlandês", "finska", "fiński"],
    "czech": ["czech", "tschechisch", "tchèque", "ceco", "checo", "tsjechisch", "tcheco", "tjeckiska", "czeski"],
    "greek": ["greek", "griechisch", "grec", "greco", "griego", "grieks", "grego", "grekiska", "grecki"],
    "romanian": ["romanian", "rumänisch", "roumain", "rumeno", "rumano", "roemeens", "romeno", "rumänska", "rumuński"],
    "hungarian": ["hungarian", "ungarisch", "hongrois", "ungherese", "húngaro", "hongaars", "ungerska", "węgierski"],
    "turkish": ["turkish", "türkisch", "turc", "turco", "turks", "turkiska", "turecki"],
    "arabic": ["arabic", "arabisch", "arabe", "arabo", "árabe", "arabiska", "arabski"],
    "chinese": ["chinese", "mandarin", "chinesisch", "chinois", "cinese", "chino", "chinees", "chinês", "kinesiska", "chiński"],
    "japanese": ["japanese", "japanisch", "japonais", "giapponese", "japonés", "japans", "japonês", "japanska", "japoński"],
    "russian": ["russian", "russisch", "russe", "russo", "ruso", "russisch", "ryska", "rosyjski"],
}

# Phrases that turn a language mention into a requirement. "{lang}" is replaced by
# an alternation of the language's variants.
LANGUAGE_REQUIREMENT_PATTERNS: list[str] = [
    # en
    r"(?:fluent|fluency|proficien\w*|native|business[- ]level|professional|excellent|strong|good|working|advanced|full professional)\W+(?:\w+\W+){0,3}?(?:{lang})",
    r"(?:{lang})\W+(?:\w+\W+){0,2}?(?:fluency|proficiency|skills|is (?:a )?(?:must|required|mandatory|essential|a plus))",
    r"(?:{lang})\s*\(?(?:c1|c2|b2|native|fluent)",
    r"(?:speak|write|communicate)\w*\W+(?:\w+\W+){0,3}?(?:{lang})",
    # de
    r"(?:{lang})\w*kenntnisse",
    r"(?:sehr gute|gute|fließende|verhandlungssichere|exzellente)\W+(?:\w+\W+){0,2}?(?:{lang})",
    # fr
    r"(?:maîtrise|maitrise|courant|bilingue|niveau)\W+(?:\w+\W+){0,3}?(?:{lang})",
    r"(?:{lang})\W+(?:courant|professionnel|indispensable|exigé|requis|obligatoire)",
    # it
    r"(?:conoscenza|padronanza|ottima|buona|fluente)\W+(?:\w+\W+){0,3}?(?:{lang})",
    r"(?:{lang})\W+(?:fluente|madrelingua|obbligatori\w*|richiest\w*)",
    # es
    r"(?:dominio|nivel|conocimiento)\W+(?:\w+\W+){0,3}?(?:{lang})",
    r"(?:{lang})\W+(?:fluido|nativo|imprescindible|requerido)",
    # nl
    r"(?:vloeiend|beheersing|goede)\W+(?:\w+\W+){0,3}?(?:{lang})",
    r"(?:{lang})\W+(?:vloeiend|is een must|vereist)",
    # pl / sv / pt
    r"(?:znajomość|biegła)\W+(?:\w+\W+){0,3}?(?:{lang})",
    r"(?:flytande|goda kunskaper)\W+(?:\w+\W+){0,3}?(?:{lang})",
    r"(?:fluência|domínio)\W+(?:\w+\W+){0,3}?(?:{lang})",
]

# --- Salary -----------------------------------------------------------------

CURRENCY = r"(?:€|eur|euro|euros|£|gbp|\$|usd|chf|sek|pln|zł|dkk|nok|huf|czk|ron)"
