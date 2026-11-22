"""Words a generated place is named from: plain lists, drawn from by `gen` with the place's
own seeded random stream. None of them is anyone's real name or address.

The setting is one country's ordinary present day, to go with its dollars: given names and
surnames common among American adults now, plain street names, and invented place, firm and
utility names. Each list keeps the length it had before these words, and each given name the
sex of the one it replaced, so that every place draws as it did and only its words differ.
"""

from __future__ import annotations

FIRST_NAMES = [
    "Amanda", "Aaron", "Andrea", "Adam", "Ashley", "Andrew", "Brittany", "Anthony", "Christina", "Brandon",
    "Courtney", "Danielle", "Brian", "Denise", "Carlos", "Emily", "Christopher", "Erin", "Heather", "Corey",
    "Jennifer", "Daniel", "Jessica", "Derek", "Kayla", "Dustin", "Kimberly", "Eric", "Kristen", "Gregory", "Laura",
    "Lauren", "Jason", "Megan", "Jeremy", "Melissa", "Jonathan", "Michelle", "Jose", "Monica", "Joshua", "Nicole",
    "Justin", "Rachel", "Rebecca", "Kevin", "Samantha", "Kyle", "Stephanie", "Luis", "Tamara", "Marcus", "Tara",
    "Tiffany", "Matthew", "Vanessa", "Nathan", "Alyssa", "Ryan", "Brianna", "Travis", "Lisa", "Tyler", "Angela",
]

# Which of `FIRST_NAMES` are borne by women and which by men, so that a brother is told from a sister.
WOMEN = frozenset({
    "Alyssa", "Amanda", "Andrea", "Angela", "Ashley", "Brianna", "Brittany", "Christina", "Courtney", "Danielle",
    "Denise", "Emily", "Erin", "Heather", "Jennifer", "Jessica", "Kayla", "Kimberly", "Kristen", "Laura", "Lauren",
    "Lisa", "Megan", "Melissa", "Michelle", "Monica", "Nicole", "Rachel", "Rebecca", "Samantha", "Stephanie",
    "Tamara", "Tara", "Tiffany", "Vanessa"})
MEN = frozenset({
    "Aaron", "Adam", "Andrew", "Anthony", "Brandon", "Brian", "Carlos", "Christopher", "Corey", "Daniel", "Derek",
    "Dustin", "Eric", "Gregory", "Jason", "Jeremy", "Jonathan", "Jose", "Joshua", "Justin", "Kevin", "Kyle", "Luis",
    "Marcus", "Matthew", "Nathan", "Ryan", "Travis", "Tyler"})

SURNAMES = [
    "Anderson", "Bennett", "Brooks", "Carter", "Coleman", "Collins", "Dawson", "Ellis", "Fischer", "Foster",
    "Garcia", "Gibson", "Hayes", "Hughes", "Jensen", "Keller", "Kowalski", "Lawson", "Lopez", "Marsh", "Meyer",
    "Morales", "Nolan", "Novak", "Ortiz", "Parker", "Porter", "Ramirez", "Reyes", "Russo", "Schultz", "Shaw",
    "Sullivan", "Thornton", "Tucker", "Walsh", "Wagner", "Webb", "Whitaker", "Wolfe", "Yates",
]

STREETS = [
    "Mill Road", "Church Street", "Maple Avenue", "Elm Street", "Oak Street", "Cedar Lane", "Pine Street",
    "Ridge Road", "Walnut Street", "Lake Road", "Hickory Lane", "School Street", "Orchard Lane", "Spring Street",
    "Bridge Street", "River Road", "Birch Street", "Park Avenue", "Water Street", "Depot Street", "Grove Street",
]

PLACES = [
    "Alder Crossing", "Bramwell Falls", "Coldwater Bend", "Dunsmore", "Elk Bend", "Fenner Ridge", "Galloway Fork",
    "Harlow Springs", "Iverby", "Kettle Bend", "Larkin Mills", "Marrow Station", "Netherby Falls", "Oak Hollow",
    "Pruett Crossing", "Ravel Springs",
]

SHOP_WORDS = [
    "Market", "Grocery", "Provisions", "Food Mart", "Pantry", "General Store", "Country Store", "Corner Market",
    "Grocers", "Fresh Foods",
]

FIRM_WORDS = [
    "Wrenfold", "Quillfield", "Brightmere", "Corvane", "Lanthorn", "Halloway", "Tessaly", "Amberlin",
]

ENERGY_WORDS = [
    "Brannock", "Ostley", "Carrowmere", "Halvren",
]

# The bank in the place is a branch of a savings bank from outside it (drawn on a stream of its own).
BANK_WORDS = [
    "Ashcombe", "Brierley", "Corwin", "Delancey", "Eastvale", "Farrington",
]
