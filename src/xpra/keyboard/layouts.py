# -*- coding: utf-8 -*-
# This file is part of Xpra.
# Copyright (C) 2011-2013 Antoine Martin <antoine@xpra.org>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

# The data for this table can be found mostly here:
# http://msdn.microsoft.com/en-us/library/aa912040.aspx
# and here:
# http://support.microsoft.com/kb/278957
# Format:
# Language identifier: (Language code, Sublanguage - locale, Language, Default code page, X11 keymap, x11 variants)
# The x11 keymap name was found in /usr/share/X11/xkb/rules/*
# This is used for converting the layout we detect using win32api into
# something that can be used by X11 (a layout with optional variant)
UNICODE=-1
LATAM_VARIANTS = ["nodeadkeys", "deadtilde", "sundeadkeys"]
ARA_VARIANTS = ["azerty", "azerty_digits", "digits", "qwerty", "qwerty_digits", "buckwalter"]
ES_VARIANTS = ["nodeadkeys", "deadtilde", "sundeadkeys", "dvorak", "est", "cat", "mac"]
RS_VARIANTS = ["yz", "latin", "latinunicode", "latinyz", "latinunicodeyz", "alternatequotes", "latinalternatequotes", "rue"]
FR_VARIANTS = ["nodeadkeys", "sundeadkeys", "oss", "oss_latin9", "oss_nodeadkeys", "oss_sundeadkeys", "latin9", "latin9_nodeadkeys", "latin9_sundeadkeys", "bepo", "bepo_latin9", "dvorak", "mac", "bre", "oci", "geo"]
WIN32_LAYOUTS = {
           1025: ("ARA", "Saudi Arabia",   "Arabic",                   1356,   "ar", []),
           1026: ("BGR", "Bulgaria",       "Bulgarian",                1251,   "bg", ["phonetic", "bas_phonetic"]),
           1027: ("CAT", "Spain",          "Catalan",                  1252,   "ad", []),
           1028: ("CHT", "Taiwan",         "Chinese",                  950,    "tw", ["indigenous", "saisiyat"]),
           1029: ("CSY", "Czech",          "Czech",                    1250,   "cz", ["bksl", "qwerty", "qwerty_bksl", "ucw", "dvorak-ucw"]),
           1030: ("DAN", "Denmark",        "Danish",                   1252,   "dk", ["nodeadkeys", "mac", "mac_nodeadkeys", "dvorak"]),
           1031: ("DEU", "Germany",        "German",                   1252,   "de", ["nodeadkeys", "sundeadkeys", "mac"]),
           1032: ("ELL", "Greece",         "Greek",                    1253,   "gr", ["simple", "extended", "nodeadkeys", "polytonic"]),
           1033: ("USA", "United States",  "English",                  1252,   "us", []),
           1034: ("ESP", "Spain (Traditional sort)", "Spanish",        1252,   "es", ES_VARIANTS),
           1035: ("FIN", "Finland",        "Finnish",                  1252,   "fi", ["classic", "nodeadkeys", "smi", "mac"]),
           1036: ("FRA", "France",         "French",                   1252,   "fr", FR_VARIANTS),
           1037: ("HEB", "Israel",         "Hebrew",                   1255,   "il", ["lyx", "phonetic", "biblical"]),
           1038: ("HUN", "Hungary",        "Hungarian",                1250,   "hu", ["standard", "nodeadkeys", "qwerty", "101_qwertz_comma_dead", "101_qwertz_comma_nodead", "101_qwertz_dot_dead", "101_qwertz_dot_nodead", "101_qwerty_comma_dead", "101_qwerty_comma_nodead", "101_qwerty_dot_dead", "101_qwerty_dot_nodead", "102_qwertz_comma_dead", "102_qwertz_comma_nodead", "102_qwertz_dot_dead", "102_qwertz_dot_nodead", "102_qwerty_comma_dead", "102_qwerty_comma_nodead", "102_qwerty_dot_dead", "102_qwerty_dot_nodead"]),
           1039: ("ISL", "Iceland",        "Icelandic",                1252,   "is", ["sundeadkeys", "nodeadkeys", "mac", "dvorak"]),
           1040: ("ITA", "Italy",          "Italian",                  1252,   "it", ["nodeadkeys", "mac", "us", "geo"]),
           1041: ("JPN", "Japan",          "Japanese",                 932,    "jp", ["kana", "kana86", "OADG109A", "mac"]),
           1042: ("KOR", "Korea",          "Korean",                   949,    "kr", ["kr104"]),
           1043: ("NLD", "Netherlands",    "Dutch",                    1252,   "nl", ["sundeadkeys", "mac", "std"]),
           1044: ("NOR", "Norway (Bokmål)","Norwegian",                1252,   "no", ["nodeadkeys", "dvorak", "smi", "smi_nodeadkeys", "mac", "mac_nodeadkeys"]),
           1045: ("PLK", "Poland",         "Polish",                   1250,   "pl", ["qwertz", "dvorak", "dvorak_quotes", "dvorak_altquotes", "csb", "ru_phonetic_dvorak", "dvp"]),
           1046: ("PTB", "Brazil",         "Portuguese",               1252,   "br", ["nodeadkeys", "dvorak", "nativo", "nativo-us", "nativo-epo"]),
           1048: ("ROM", "Romania",        "Romanian",                 1250,   "ro", ["cedilla", "std", "std_cedilla", "winkeys"]),
           1049: ("RUS", "Russia",         "Russian",                  1251,   "ru", ["phonetic", "phonetic_winkeys", "typewriter", "legacy", "typewriter-legacy", "tt", "os_legacy", "os_winkeys", "cv", "cv_latin", "udm", "kom", "sah", "xal", "dos", "srp", "bak", "chm"]),
           1050: ("HRV", "Croatia",        "Croatian",                 1250,   "hr", ["alternatequotes", "unicode", "unicodeus", "us"]),
           1051: ("SKY", "Slovakia",       "Slovakian",                1250,   "sk", ["bksl", "qwerty", "qwerty_bksl"]),
           1052: ("SQI", "Albania",        "Albanian",                 1250,   "al", []),
           1053: ("SVE", "Sweden",         "Swedish",                  1252,   "se", ["nodeadkeys", "dvorak", "rus", "rus_nodeadkeys", "smi", "mac", "svdvorak", "swl"]),
           1054: ("THA", "Thailand",       "Thai",                     874,    "th", ["tis", "pat"]),
           1055: ("TRK", "Turkey",         "Turkish",                  1254,   "tr", ["f", "alt", "sundeadkeys", "ku", "ku_f", "ku_alt", "intl", "crh", "crh_f", "crh_alt"]),
           1056: ("URP", "Pakistan",       "Urdu",                     1256,   "pk", ["urd-crulp", "urd-nla", "ara", "snd"]),
           1057: ("IND", "Indonesia (Bahasa)", "Indonesian",           1252,   "", []),
           1058: ("UKR", "Ukraine",        "Ukrainian",                1251,   "ua", ["phonetic", "typewriter", "winkeys", "legacy", "rstu", "rstu_ru", "homophonic"]),
           1059: ("BEL", "Belarus",        "Belarusian",               1251,   "by", ["legacy", "latin"]),
           1060: ("SLV", "Slovenia",       "Slovenian",                1250,   "si", ["alternatequotes", "us"]),
           1061: ("ETI", "Estonia",        "Estonian",                 1257,   "ee", ["nodeadkeys", "dvorak", "us"]),
           1062: ("LVI", "Latvia",         "Latvian",                  1257,   "lv", ["apostrophe", "tilde", "fkey", "modern", "ergonomic", "adapted"]),
           1063: ("LTH", "Lithuania",      "Lithuanian",               1257,   "lt", ["std", "us", "ibm", "lekp", "lekpa"]),
           1065: ("FAR", "Iran",           "Farsi",                    1256,   "", []),
           1066: ("VIT", "Viet Nam",       "Vietnamese",               1258,   "vn", []),
           1067: ("HYE", "Armenia",        "Armenian",                 UNICODE,"am", ["phonetic", "phonetic-alt", "eastern", "western", "eastern-alt"]),
           1068: ("AZE", "Azerbaijan (Latin)", "Azeri",                1254,   "az", ["cyrillic"]),
           1069: ("EUQ", "Spain",          "Basque",                   1252,   "es", []),
           1071: ("MKI", "F.Y.R.O. Macedonia", "F.Y.R.O. Macedonia",   1251,   "mk", ["nodeadkeys"]),
           1078: ("AFK", "South Africa",   "Afrikaans",                1252,   "", []),
           1079: ("KAT", "Georgia",        "Georgian",                 UNICODE,"ge", ["ergonomic", "mess", "ru", "os"]),
           1080: ("FOS", "Faroe Islands",  "Faroese",                  1252,   "fo", ["nodeadkeys"]),
           1081: ("HIN", "India",          "Hindi",                    UNICODE,"in", ["bolnagri", "hin-wx"]),
           1086: ("MSL", "Malaysia",       "Malay",                    1252,   "in", ["mal", "mal_lalitha", "mal_enhanced"]),
           1087: ("KKZ", "Kazakstan",      "Kazakh",                   1251,   "kz", ["ruskaz", "kazrus"]),
           1088: ("KYR", "Kyrgyzstan",     "Kyrgyz",                   1251,   "kg", ["phonetic"]),
           1089: ("SWK", "Kenya",          "Swahili",                  1252,   "ke", ["kik"]),
           1091: ("UZB", "Uzbekistan (Latin)", "Uzbek",                1254,   "uz", ["latin"]),
           1092: ("TTT", "Tatarstan",      "Tatar",                    1251,   "ru", ["tt"]),
           1094: ("PAN", "India (Gurmukhi script)", "Punjabi",         UNICODE,"in", ["guru", "jhelum"]),
           1095: ("GUJ", "India",          "Gujarati",                 UNICODE,"in", ["guj"]),
           1097: ("TAM", "India",          "Tamil",                    UNICODE,"in", ["tam_unicode", "tam_keyboard_with_numerals", "tam_TAB", "tam_TSCII", "tam"]),
           1098: ("TEL", "India (Telugu script)", "Telugu",            UNICODE,"in", ["tel"]),
           1099: ("KAN", "India (Kannada script)", "Kannada",          UNICODE,"in", ["kan"]),
           1102: ("MAR", "India",          "Marathi",                  UNICODE,"in", []),
           1103: ("SAN", "India",          "Sanskrit",                 UNICODE,"in", []),
           1104: ("MON", "Mongolia",       "Mongolian (Cyrillic)",     1251,   "mn", []),
           1110: ("GLC", "Spain",          "Galician",                 1252,   "es", []),
           1111: ("KNK", "India",          "Konkani",                  UNICODE,"in", []),
           1114: ("SYR", "Syria",          "Syriac",                   UNICODE,"sy", ["syc", "syc_phonetic", "ku", "ku_f", "ku_alt"]),
           1125: ("DIV", "Maldives",       "Divehi",                   UNICODE,"", []),
           2049: ("ARI", "Iraq",           "Arabic",                   1256,   "iq", ["ku", "ku_f", "ku_alt", "ku_ara"]),
           2052: ("CHS", "PRC",            "Chinese, Simplified",      0,      "cn", ["tib", "tib_asciinum", "uig"]),
           2055: ("DES", "Switzerland",    "German",                   1252,   "de", ["deadacute", "deadgraveacute", "nodeadkeys", "ro", "ro_nodeadkeys", "dvorak", "sundeadkeys", "neo", "mac", "mac_nodeadkeys", "dsb", "dsb_qwertz", "qwerty", "ru"]),
           2057: ("ENG", "UK",             "English",                  1252,   "gb", ["extd", "intl", "dvorak", "dvorakukp", "mac", "mac_intl", "colemak"]),
           2058: ("ESM", "Mexico",         "Spanish",                  1252,   "es", ES_VARIANTS),
           2060: ("FRB", "Benelux",        "French",                   1252,   "be", ["oss", "oss_latin9", "oss_sundeadkeys", "iso-alternate", "nodeadkeys", "sundeadkeys", "wang"]),
           2064: ("ITS", "Switzerland",    "Italian",                  1252,   "it", ["nodeadkeys", "mac", "us", "geo"]),
           2067: ("NLB", "Belgium",        "Dutch",                    1252,   "nl", ["sundeadkeys", "mac", "std"]),
           2068: ("NON", "Norway (Nynorsk)", "Norwegian",              1252,   "no", ["nodeadkeys", "dvorak", "smi", "smi_nodeadkeys", "mac", "mac_nodeadkeys"]),
           2070: ("PTG", "Portugal",       "Portuguese",               1252,   "pt", ["nodeadkeys", "sundeadkeys", "mac", "mac_nodeadkeys", "mac_sundeadkeys", "nativo", "nativo-us", "nativo-epo"]),
           2074: ("SRL", "Serbia (Latin)", "Serbian",                  1250,   "rs", RS_VARIANTS),
           2077: ("SVF", "Finland",        "Swedish",                  1252,   "se", ["nodeadkeys", "dvorak", "rus", "rus_nodeadkeys", "smi", "mac", "svdvorak", "swl"]),
           2092: ("AZE", "Azerbaijan (Cyrillic)", "Azeri",             1251,   "az", ["cyrillic"]),
           2110: ("MSB", "Brunei Darussalam", "Malay",                 1252,   "in", ["mal", "mal_lalitha", "mal_enhanced"]),
           2115: ("UZB", "Uzbekistan (Cyrillic)", "Uzbek",             1251,   "uz", ["latin"]),
           3073: ("ARE", "Egypt",          "Arabic",                   1256,   "ara", ARA_VARIANTS),
           3076: ("ZHH", "Hong Kong SAR",  "Chinese",                  950,    "cn", []),
           3079: ("DEA", "Austria",        "German",                   1252,   "at", ["nodeadkeys", "sundeadkeys", "mac"]),
           3081: ("ENA", "Australia",      "English",                  1252,   "us", []),
           3082: ("ESN", "Spain (International sort)", "Spanish",      1252,   "es", ES_VARIANTS),
           3084: ("FRC", "Canada",         "French",                   1252,   "ca", ["fr-dvorak", "fr-legacy", "multix", "multi", "multi-2gr", "ike"]),
           3098: ("SRB", "Serbia (Cyrillic)", "Serbian",               1251,   "", RS_VARIANTS),
           4097: ("ARL", "Libya",          "Arabic",                   1256,   "ara", ARA_VARIANTS),
           4100: ("ZHI", "Singapore",      "Chinese",                  936,    "cn", []),
           4103: ("DEL", "Luxembourg",     "German",                   1252,   "de", []),
           4105: ("ENC", "Canada",         "English",                  1252,   "ca", ["eng"]),
           4106: ("ESG", "Guatemala",      "Spanish",                  1252,   "latam", LATAM_VARIANTS),
           4108: ("FRS", "Switzerland",    "French",                   1252,   "ch", ["fr", "fr_nodeadkeys", "fr_sundeadkeys", "fr_mac"]),
           5121: ("ARG", "Algeria",        "Arabic",                   1256,   "ara", ARA_VARIANTS),
           5124: ("ZHM", "Macao SAR",      "Chinese",                  950,    "cn", []),
           5127: ("DEC", "Liechtenstein",  "German",                   1252,   "de", []),
           5129: ("ENZ", "New Zealand",    "English",                  1252,   "us", []),
           5130: ("ESC", "Costa Rica",     "Spanish",                  1252,   "latam", LATAM_VARIANTS),
           5132: ("FRL", "Luxembourg",     "French",                   1252,   "fr", FR_VARIANTS),
           6145: ("ARM", "Morocco",        "Arabic",                   1256,   "ara", ARA_VARIANTS),
           6153: ("ENI", "Ireland",        "English",                  1252,   "en", []),
           6154: ("ESA", "Panama",         "Spanish",                  1252,   "latam", LATAM_VARIANTS),
           6156: ("FRM", "Monaco",         "French",                   1252,   "fr", FR_VARIANTS),
           7169: ("ART", "Tunisia",        "Arabic",                   1256,   "ara", ARA_VARIANTS),
           7177: ("ENS", "South Africa",   "English",                  1252,   "en", []),
           7178: ("ESD", "Dominican Republic", "Spanish",              1252,   "latam", LATAM_VARIANTS),
           8193: ("ARO", "Oman",           "Arabic",                   1256,   "ara", ARA_VARIANTS),
           8201: ("ENJ", "Jamaica",        "English",                  1252,   "en", []),
           8202: ("ESV", "Venezuela",      "Spanish",                  1252,   "latam", LATAM_VARIANTS),
           9217: ("ARY", "Yemen",          "Arabic",                   1256,   "ara", ARA_VARIANTS),
           9225: ("ENB", "Caribbean",      "English",                  1252,   "en", []),
           9226: ("ESO", "Colombia",       "Spanish",                  1252,   "latam", LATAM_VARIANTS),
           10241: ("ARS", "Syria",         "Arabic",                   1256,   "sy", ["syc", "syc_phonetic"]),
           10249: ("ENL", "Belize",        "English",                  1252,   "us", []),
           10250: ("ESR", "Peru",          "Spanish",                  1252,   "latam", LATAM_VARIANTS),
           11265: ("ARJ", "Jordan",        "Arabic",                   1256,   "ara", ARA_VARIANTS),
           11273: ("ENT", "Trinidad",      "English",                  1252,   "us", []),
           11274: ("ESS", "Argentina",     "Spanish",                  1252,   "latam", LATAM_VARIANTS),
           12289: ("ARB", "Lebanon",       "Arabic",                   1256,   "ara", ARA_VARIANTS),
           12297: ("ENW", "Zimbabwe",      "English",                  1252,   "us", []),
           12298: ("ESF", "Ecuador",       "Spanish",                  1252,   "latam", LATAM_VARIANTS),
           13321: ("ENP", "Philippines",   "English",                  1252,   "us", []),
           13313: ("ARK", "Kuwait",        "Arabic",                   1256,   "ara", ARA_VARIANTS),
           13322: ("ESL", "Chile",         "Spanish",                  1252,   "latam", LATAM_VARIANTS),
           14337: ("ARU", "U.A.E.",        "Arabic",                   1256,   "ara", ARA_VARIANTS),
           14345: ("",    "Indonesia",     "English",                  1252,   "us", []),
           14346: ("ESY", "Uruguay",       "Spanish",                  1252,   "latam", LATAM_VARIANTS),
           15361: ("ARH", "Bahrain",       "Arabic",                   1256,   "ara", ARA_VARIANTS),
           15369: ("ZHH", "Hong Kong SAR", "English",                  1252,   "us", []),
           15370: ("ESZ", "Paraguay",      "Spanish",                  1252,   "latam", LATAM_VARIANTS),
           16385: ("ARQ", "Qatar",         "Arabic",                   1256,   "ara", ARA_VARIANTS),
           16393: ("",    "India",         "English",                  1252,   "us", []),
           16394: ("ESB", "Bolivia",       "Spanish",                  1252,   "latam", LATAM_VARIANTS),
           17417: ("",    "Malaysia",      "English",                  1252,   "us", []),
           17418: ("ESE", "El Salvador",   "Spanish",                  1252,   "latam", LATAM_VARIANTS),
           18441: ("",    "Singapore",     "English",                  1252,   "us", []),
           18442: ("ESH", "Honduras",      "Spanish",                  1252,   "latam", LATAM_VARIANTS),
           19466: ("ESI", "Nicaragua",     "Spanish",                  1252,   "latam", LATAM_VARIANTS),
           20490: ("ESU", "Puerto Rico",   "Spanish",                  1252,   "latam", LATAM_VARIANTS),
           58378: ("",    "LatAm",         "Spanish",                  1252,   "latam", LATAM_VARIANTS),
           58380: ("",    "North Africa",  "French",                   1252,   "fr", FR_VARIANTS),
           }

#map win32 keyboard codes to x11 names:
#based on
#https://docs.microsoft.com/en-us/windows-hardware/manufacture/desktop/windows-language-pack-default-values
WIN32_KEYBOARDS = {
    0x0000041c  : "al",     #Albania
    0x00000401  : "ar",     #Arabic (101)
    0x00010401  : "ar",     #Arabic (102)
    0x00020401  : "ar",     #Arabic (102) AZERTY
    0x0000042b  : "am",     #Armenian Eastern
    0x0002042b  : "am",     #Armenian Phonetic
    0x0003042b  : "am",     #Armenian Typewriter
    0x0001042b  : "am",     #Armenian Western
    0x0000044d  : "in",     #Assamese - Inscript
    0x0001042c  : "az",     #Azerbaijani (Standard)
    0x0000082c  : "az",     #Azerbaijani Cyrillic
    0x0000042c  : "az",     #Azerbaijani Latin
    0x0000046d  : "ru",     #Bashkir
    0x00000423  : "by",     #Belarusian
    0x0001080c  : "be",     #Belgian (Comma)
    0x00000813  : "be",     #Belgian (Period)
    0x0000080c  : "be",     #Belgian French
    0x00000445  : "bd",     #Bangla (Bangladesh)
    0x00020445  : "bd",     #Bangla (India)
    0x00010445  : "bd",     #Bangla (India - Legacy)
    0x0000201a  : "ba",     #Bosnian (Cyrillic)
    #0x000b0c00  : "",       #Buginese
    0x00030402  : "bg",     #Bulgarian
    0x00010402  : "bg",     #Bulgarian (Latin)
    0x00020402  : "bg",     #Bulgarian (phonetic layout)
    0x00040402  : "bg",     #Bulgarian (phonetic traditional)
    0x00000402  : "bg",     #Bulgarian (Typewriter)
    0x00001009  : "ca",     #Canadian French
    0x00000c0c  : "ca",     #Canadian French (Legacy)
    0x00011009  : "ca",     #Canadian Multilingual Standard
    0x0000085f  : "fr",     #Central Atlas Tamazight
    0x00000429  : "ku",     #Central Kurdish
    0x0000045c  : "us",     #Cherokee Nation
    0x0001045c  : "us",     #Cherokee Nation Phonetic
    0x00000804  : "cn",     #Chinese (Simplified) - US Keyboard
    0x00000404  : "cn",     #Chinese (Traditional) - US Keyboard
    0x00000c04  : "cn",     #Chinese (Traditional, Hong Kong S.A.R.)
    0x00001404  : "cn",     #Chinese (Traditional Macao S.A.R.) US Keyboard
    0x00001004  : "cn",     #Chinese (Simplified, Singapore) - US keyboard
    0x0000041a  : "hr",     #Croatian
    0x00000405  : "cz",     #Czech
    0x00010405  : "cz",     #Czech (QWERTY)
    0x00020405  : "cz",     #Czech Programmers
    0x00000406  : "dk",     #Danish
    0x00000439  : "in",     #Devanagari-INSCRIPT
    0x00000465  : "in",     #Divehi Phonetic
    0x00010465  : "in",     #Divehi Typewriter
    0x00000413  : "nl",     #Dutch
    0x00000C51  : "dz",     #Dzongkha
    0x00000425  : "ee",     #Estonian
    0x00000438  : "fo",     #Faeroese
    0x0000040b  : "fi",     #Finnish
    0x0001083b  : "fi",     #Finnish with Sami
    0x0000040c  : "fr",     #French
    #0x00120c00  : "??",     #Futhark
    0x00000437  : "ge",     #Georgian
    0x00020437  : "ge",     #Georgian (Ergonomic)
    0x00010437  : "ge",     #Georgian (QWERTY)
    0x00030437  : "ge",     #Georgian Ministry of Education and Science Schools
    0x00040437  : "ge",     #Georgian (Old Alphabets)
    0x00000407  : "de",     #German
    0x00010407  : "de",     #German (IBM)
    #0x000c0c00  : "??",     #Gothic
    0x00000408  : "gr",     #Greek
    0x00010408  : "gr",     #Greek (220)
    0x00030408  : "gr",     #Greek (220) Latin
    0x00020408  : "gr",     #Greek (319)
    0x00040408  : "gr",     #Greek (319) Latin
    0x00050408  : "gr",     #Greek Latin
    0x00060408  : "gr",     #Greek Polytonic
    #0x0000046f  : "??",     #Greenlandic
    #0x00000474  : "??",     #Guarani
    0x00000447  : "in",     #Gujarati
    0x00000468  : "gh",     #Hausa
    0x0000040d  : "il",     #Hebrew
    0x00010439  : "in",     #Hindi Traditional
    0x0000040e  : "hu",     #Hungarian
    0x0001040e  : "hu",     #Hungarian 101-key
    0x0000040f  : "is",     #Icelandic
    0x00000470  : "ng",     #Igbo
    0x00004009  : "in",     #India
    0x0000085d  : "ca",     #Inuktitut - Latin
    0x0001045d  : "ca",     #Inuktitut - Naqittaut
    0x00001809  : "ie",     #Ireland
    0x00000410  : "it",     #Italian
    0x00010410  : "it",     #Italian
    0x00000411  : "jp",     #Japanese
    #0x00110c00  : "??",     #Javanese
    0x0000044b  : "in",     #Kannada
    0x0000043f  : "kz",     #Kazakh
    0x00000453  : "kh",     #Khmer
    0x00010453  : "kh",     #Khmer (NIDA)
    0x00000412  : "kr",     #Korean
    0x00000440  : "kg",     #Kyrgyz Cyrillic
    0x00000454  : "la",     #Lao
    0x0000080a  : "latam",  #Latin American
    0x00020426  : "lv",     #Latvian (Standard)
    0x00010426  : "lv",     #Latvian (Legacy)
    #0x00070c00  : "??",     #Lisu (Basic)
    #0x00080c00  : "??",     #Lisu (Standard)
    0x00010427  : "lt",     #Lithuanian
    0x00000427  : "lt",     #Lithuanian IBM
    0x00020427  : "lt",     #Lithuanian Standard
    0x0000046e  : "de",     #Luxembourgish
    0x0000042f  : "mk",     #Macedonia (FYROM)
    0x0001042f  : "mk",     #Macedonia (FYROM) - Standard
    0x0000044c  : "in",     #Malayalam
    0x0000043a  : "mt",     #Maltese 47-Key
    0x0001043a  : "mt",     #Maltese 48-key
    0x00000481  : "mao",    #Maori
    0x0000044e  : "in",     #Marathi
    0x00000850  : "mn",     #Mongolian (Mongolian Script - Legacy)
    0x00020850  : "mn",     #Mongolian (Mongolian Script - Standard)
    0x00000450  : "mn",     #Mongolian Cyrillic
    0x00010c00  : "mm",     #Myanmar
    0x00090c00  : "??",     #N'ko
    0x00000461  : "np",     #Nepali
    0x00020c00  : "th",     #New Tai Lue
    0x00000414  : "no",     #Norwegian
    0x0000043b  : "no",     #Norwegian with Sami
    0x00000448  : "in",     #Odia
    0x000d0c00  : "in",     #Ol Chiki    
    }

# This is generated from the table above so we can
# let the user choose his own layout.
# (country,language) : (layout,variant)
X11_LAYOUTS = {}
for _, country, language, _, layout, variants in WIN32_LAYOUTS.values():
    key = (country,language)
    value = (layout, variants)
    X11_LAYOUTS[key] = value
LAYOUT_VARIANTS = {}
for _, _, _, _, layout, variants in WIN32_LAYOUTS.values():
    l = LAYOUT_VARIANTS.get(layout)
    if not l:
        l = []
        LAYOUT_VARIANTS[layout] = l
    for variant in variants:
        if variant not in l:
            l.append(variant)

def parse_xkbmap_query(xkbmap_query):
    """ parses the output of "setxkbmap -query" into a dict """
    import re
    settings = {}
    opt_re = re.compile(r"(\w*):\s*(.*)")
    for line in xkbmap_query.splitlines():
        m = opt_re.match(line)
        if m:
            v = m.group(2).strip()
            if v!=",":
                settings[m.group(1)] = v
    return settings

def xkbmap_query_tostring(query_dict):
    """ converts an xkb query dict back into a string """
    s = ""
    for k in ("rules", "model", "layout", "variant", "options"):
        if k in query_dict:
            v = query_dict.get(k)
            s += (str(k)+":").ljust(12)+str(v)+"\n"
    return s
