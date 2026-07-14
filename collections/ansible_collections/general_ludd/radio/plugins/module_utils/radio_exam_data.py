"""
radio_exam_data -- Structured Q&A for ham (FCC Technician/General/Extra)
and marine (ROC-M, GMDSS) license examinations.

Data shape per question:
    {
        "id": str,
        "exam": "fcc_tech" | "fcc_general" | "fcc_extra" | "roc_m" | "gmdss",
        "section": str,
        "text": str,
        "choices": list[str],
        "correct": int,
        "explanation": str,
    }

Functions:
    get_questions(exam, count) -> list of Question
    grade_exam(answers) -> score + explanations for wrong answers
"""

from __future__ import annotations

import random
from typing import Any

EXAM_QUESTIONS: list[dict[str, Any]] = [
    # ── FCC Technician (T1 -- FCC Rules, station license, operator license) ──
    {
        "id": "T1A01",
        "exam": "fcc_tech",
        "section": "T1",
        "text": "Which of the following is a purpose of the Amateur Radio Service as stated in the FCC rules and regulations?",
        "choices": [
            "Providing personal radio communications for profit",
            "Advancing skills in the technical and communication phases of the radio art",
            "Providing commercial broadcast services",
            "Enabling encrypted government communications",
        ],
        "correct": 1,
        "explanation": "FCC Part 97.1 defines the basis and purpose: advancing skills in both the technical and communication phases of the radio art.",
    },
    {
        "id": "T1A02",
        "exam": "fcc_tech",
        "section": "T1",
        "text": "Which agency regulates and enforces the rules for the Amateur Radio Service in the United States?",
        "choices": [
            "FEMA",
            "Homeland Security",
            "The FCC",
            "The NTIA",
        ],
        "correct": 2,
        "explanation": "The Federal Communications Commission (FCC) regulates all non-federal radio spectrum in the US, including amateur radio under Part 97.",
    },
    {
        "id": "T1B01",
        "exam": "fcc_tech",
        "section": "T1",
        "text": "What is the ITU?",
        "choices": [
            "An agency of the United States Department of Telecommunications Management",
            "A United Nations agency for information and communication technology issues",
            "An independent frequency coordination agency",
            "A department of the FCC",
        ],
        "correct": 1,
        "explanation": "The International Telecommunication Union (ITU) is a UN specialized agency that coordinates global telecommunication networks and services.",
    },
    {
        "id": "T1B02",
        "exam": "fcc_tech",
        "section": "T1",
        "text": "Which amateur radio stations may make contact with an amateur radio station on the International Space Station (ISS) using 2 meter and 70 cm band frequencies?",
        "choices": [
            "Only Extra class stations",
            "Only stations with satellite endorsement",
            "Any amateur holding a Technician or higher license",
            "Only General class or higher",
        ],
        "correct": 2,
        "explanation": "The ISS amateur radio station operates under reciprocal agreements. Any Technician or higher licensee may communicate using these VHF/UHF frequencies.",
    },
    {
        "id": "T1C01",
        "exam": "fcc_tech",
        "section": "T1",
        "text": "Which of the following is a valid Technician class call sign format?",
        "choices": [
            "KA1ABC",
            "WXX1AB",
            "K1ABC",
            "All of these choices are correct",
        ],
        "correct": 3,
        "explanation": "All listed formats are valid FCC amateur call sign formats. 1x2, 2x1, 2x2, and 1x3 call signs exist across various call areas.",
    },
    {
        "id": "T1D01",
        "exam": "fcc_tech",
        "section": "T1",
        "text": "With which countries are FCC-licensed amateur stations prohibited from exchanging communications?",
        "choices": [
            "Any country whose administration has notified the ITU that it objects to such communications",
            "Any country that is not a member of the ITU",
            "Any country that has no diplomatic relations with the US",
            "Any country that does not have a reciprocal amateur radio agreement",
        ],
        "correct": 0,
        "explanation": "Per FCC rules, amateur stations may not exchange communications with countries whose administrations have formally objected via ITU notification.",
    },
    {
        "id": "T1E01",
        "exam": "fcc_tech",
        "section": "T1",
        "text": "When is an amateur station permitted to transmit without a control operator?",
        "choices": [
            "When using automatic control, such as a repeater",
            "When the station is operating under remote control",
            "Never",
            "When transmitting RTTY or data emissions",
        ],
        "correct": 2,
        "explanation": "Every amateur station must have a control operator designated at all times. Even remotely controlled or automatically controlled stations require a designated control operator.",
    },
    {
        "id": "T1F01",
        "exam": "fcc_tech",
        "section": "T1",
        "text": "What type of identification is being used when identifying a station on the air as 'Race Headquarters'?",
        "choices": [
            "Tactical call sign",
            "An official call sign reserved for RACES drills",
            "SSID",
            "Broadcast station identification",
        ],
        "correct": 0,
        "explanation": "Tactical call signs (like 'Race Headquarters' or 'Net Control') may be used during events, but the station's FCC-issued call sign must also be transmitted every 10 minutes.",
    },
    # ── FCC Technician (T2 -- Operating Procedures) ──
    {
        "id": "T2A01",
        "exam": "fcc_tech",
        "section": "T2",
        "text": "What is the most common repeater frequency offset in the 2 meter band?",
        "choices": [
            "Plus or minus 500 kHz",
            "Plus or minus 600 kHz",
            "Plus or minus 5 MHz",
            "Plus or minus 2 MHz",
        ],
        "correct": 1,
        "explanation": "The standard repeater offset in the 2 meter band (144-148 MHz) is 600 kHz. Repeaters listen on one frequency and transmit on another offset by this amount.",
    },
    {
        "id": "T2B01",
        "exam": "fcc_tech",
        "section": "T2",
        "text": "Which of the following is true concerning the use of phonetic alphabet during station identification?",
        "choices": [
            "It is optional, but commonly used to ensure clarity",
            "It is required by FCC rules",
            "It is only required on HF bands",
            "It is prohibited on VHF/UHF repeaters",
        ],
        "correct": 0,
        "explanation": "The use of a phonetic alphabet (Alpha, Bravo, Charlie, etc.) for station identification is optional but encouraged when conditions make plain voice unclear.",
    },
    {
        "id": "T2C01",
        "exam": "fcc_tech",
        "section": "T2",
        "text": "When do the FCC rules NOT apply to the operation of an amateur station?",
        "choices": [
            "When operating a RACES station during a disaster",
            "When operating under special temporary authority",
            "Never -- FCC rules always apply",
            "When operating on board a US-registered vessel in international waters",
        ],
        "correct": 2,
        "explanation": "FCC rules always apply to stations licensed by the FCC, regardless of location, purpose, or operating conditions.",
    },
    # ── FCC Technician (T3 -- Radio wave characteristics) ──
    {
        "id": "T3A01",
        "exam": "fcc_tech",
        "section": "T3",
        "text": "What should you do if another operator reports that your station's 2 meter signals are strong and distorted?",
        "choices": [
            "Change the batteries in your microphone",
            "Speak louder into the microphone",
            "Reduce your transmit audio level or deviation",
            "Switch to simplex operation",
        ],
        "correct": 2,
        "explanation": "Strong but distorted signals typically indicate over-deviation (too much modulation). Reduce the microphone gain or deviation level.",
    },
    {
        "id": "T3B01",
        "exam": "fcc_tech",
        "section": "T3",
        "text": "What is the velocity of a radio wave traveling through free space?",
        "choices": [
            "Speed of light (approximately 300 million meters per second)",
            "Speed of sound (approximately 340 meters per second)",
            "150 million meters per second",
            "Varies with frequency",
        ],
        "correct": 0,
        "explanation": "All electromagnetic waves travel at the speed of light in free space: c ~ 3.0 x 10^8 m/s. The relationship is wavelength = c / frequency.",
    },
    {
        "id": "T3C01",
        "exam": "fcc_tech",
        "section": "T3",
        "text": "Why are direct UHF signals rarely heard from stations outside your local coverage area?",
        "choices": [
            "UHF signals are absorbed by the ionosphere",
            "They are usually not reflected by the ionosphere",
            "UHF signals are too weak to travel far",
            "FCC rules limit UHF transmitter power",
        ],
        "correct": 1,
        "explanation": "UHF signals (300 MHz - 3 GHz) typically pass through the ionosphere rather than being reflected back to Earth. This limits them to line-of-sight propagation.",
    },
    # ── FCC Technician (T4 -- Amateur radio practices and station setup) ──
    {
        "id": "T4A01",
        "exam": "fcc_tech",
        "section": "T4",
        "text": "Which of the following is an appropriate power supply rating for a typical 50 watt output mobile FM transceiver?",
        "choices": [
            "13.8 volts at 4 amperes",
            "13.8 volts at 12 amperes",
            "24 volts at 4 amperes",
            "24 volts at 8 amperes",
        ],
        "correct": 1,
        "explanation": "A 50W output transmitter with 50 percent efficiency draws around 100W input. At 13.8V, that's roughly 7-8A. A 12A supply provides adequate headroom.",
    },
    {
        "id": "T4B01",
        "exam": "fcc_tech",
        "section": "T4",
        "text": "What is the effect of introducing an inductor in series with a signal path?",
        "choices": [
            "It passes low frequencies more readily than high frequencies",
            "It passes high frequencies more readily than low frequencies",
            "It attenuates all frequencies equally",
            "It amplifies signals at the resonant frequency",
        ],
        "correct": 0,
        "explanation": "An inductor's impedance increases with frequency (XL = 2*pi*f*L), so it acts as a low-pass filter when placed in series: low frequencies pass more easily than high frequencies.",
    },
    # ── FCC Technician (T5 -- Electrical principles) ──
    {
        "id": "T5A01",
        "exam": "fcc_tech",
        "section": "T5",
        "text": "Electrical current is measured in which of the following units?",
        "choices": [
            "Volts",
            "Watts",
            "Ohms",
            "Amperes",
        ],
        "correct": 3,
        "explanation": "Current (the flow of electric charge) is measured in amperes (amps). One ampere equals one coulomb per second.",
    },
    {
        "id": "T5B01",
        "exam": "fcc_tech",
        "section": "T5",
        "text": "How many milliamperes is 1.5 amperes?",
        "choices": [
            "15 milliamperes",
            "150 milliamperes",
            "1500 milliamperes",
            "15,000 milliamperes",
        ],
        "correct": 2,
        "explanation": "Milli- means one-thousandth, so 1 ampere = 1000 milliamperes. Therefore 1.5 A = 1500 mA.",
    },
    {
        "id": "T5C01",
        "exam": "fcc_tech",
        "section": "T5",
        "text": "What is the ability to store energy in an electric field called?",
        "choices": [
            "Inductance",
            "Resistance",
            "Tolerance",
            "Capacitance",
        ],
        "correct": 3,
        "explanation": "Capacitance is the ability to store energy in an electric field, measured in farads. A capacitor stores charge between two conductors separated by a dielectric.",
    },
    {
        "id": "T5D01",
        "exam": "fcc_tech",
        "section": "T5",
        "text": "What is the formula used to calculate electrical power in a DC circuit?",
        "choices": [
            "Power (P) equals voltage (E) multiplied by current (I)",
            "Power (P) equals voltage (E) squared divided by current (I)",
            "Power (P) equals voltage (E) divided by resistance (R)",
            "Power (P) equals current (I) divided by voltage (E)",
        ],
        "correct": 0,
        "explanation": "Ohm's Law for power: P = E x I. Power in watts equals voltage in volts multiplied by current in amperes.",
    },
    # ── FCC Technician (T6 -- Electrical components) ──
    {
        "id": "T6A01",
        "exam": "fcc_tech",
        "section": "T6",
        "text": "What electrical component opposes the flow of current in a DC circuit?",
        "choices": [
            "Inductor",
            "Resistor",
            "Voltage regulator",
            "Capacitor",
        ],
        "correct": 1,
        "explanation": "A resistor opposes the flow of electric current. Resistance is measured in ohms, and it dissipates electrical energy as heat.",
    },
    {
        "id": "T6B01",
        "exam": "fcc_tech",
        "section": "T6",
        "text": "What type of semiconductor device is used for amplification and switching in many amateur radio circuits?",
        "choices": [
            "Diode",
            "Transistor",
            "Varactor",
            "Zener diode",
        ],
        "correct": 1,
        "explanation": "Transistors are three-terminal semiconductor devices (base, collector, emitter for BJT) that can both amplify signals and act as electronic switches.",
    },
    {
        "id": "T6C01",
        "exam": "fcc_tech",
        "section": "T6",
        "text": "What type of component is often used as an adjustable volume control?",
        "choices": [
            "Fixed resistor",
            "Power resistor",
            "Potentiometer",
            "Transformer",
        ],
        "correct": 2,
        "explanation": "A potentiometer is a variable resistor with three terminals. It functions as an adjustable voltage divider, commonly used for volume and other controls.",
    },
    # ── FCC Technician (T7 -- Station equipment, common problems) ──
    {
        "id": "T7A01",
        "exam": "fcc_tech",
        "section": "T7",
        "text": "What term describes the ability of a receiver to detect the presence of a signal?",
        "choices": [
            "Linearity",
            "Sensitivity",
            "Selectivity",
            "Stability",
        ],
        "correct": 1,
        "explanation": "Sensitivity describes a receiver's ability to detect weak signals. It is typically specified as the minimum signal level (in uV or dBm) needed for a given signal-to-noise ratio.",
    },
    {
        "id": "T7B01",
        "exam": "fcc_tech",
        "section": "T7",
        "text": "What can you do if the RF output of your transmitter is interfering with nearby electronic devices?",
        "choices": [
            "Increase transmitter power to overcome the interference",
            "Install an RF choke (ferrite bead) on affected device cables",
            "Operate only on simplex frequencies",
            "Change to a different repeater offset",
        ],
        "correct": 1,
        "explanation": "Common-mode RF currents on cables can be suppressed with ferrite chokes/beads, which increase the impedance on the cable's outer shield at RF frequencies.",
    },
    # ── FCC Technician (T8 -- Modulation modes, amateur satellite, operating activities) ──
    {
        "id": "T8A01",
        "exam": "fcc_tech",
        "section": "T8",
        "text": "Which of the following is a form of amplitude modulation?",
        "choices": [
            "Spread spectrum",
            "Packet radio",
            "Single sideband",
            "Phase shift keying",
        ],
        "correct": 2,
        "explanation": "Single sideband (SSB) is a form of amplitude modulation where the carrier and one sideband are suppressed, leaving only one sideband. It is more spectrum-efficient than full AM.",
    },
    {
        "id": "T8B01",
        "exam": "fcc_tech",
        "section": "T8",
        "text": "What telemetry information is typically transmitted by satellite beacons?",
        "choices": [
            "Audio from the satellite's receivers",
            "The satellite's orbital parameters only",
            "Health and status data such as temperature and battery voltage",
            "Encrypted command and control signals",
        ],
        "correct": 2,
        "explanation": "Amateur satellite beacons transmit telemetry (health and status information) including battery voltage, temperature, and solar panel current, typically using CW, AFSK, or AX.25 protocols.",
    },
    # ── FCC Technician (T9 -- Antennas and feed lines) ──
    {
        "id": "T9A01",
        "exam": "fcc_tech",
        "section": "T9",
        "text": "What is a beam antenna?",
        "choices": [
            "An antenna built from aluminum I-beams",
            "An omnidirectional antenna invented by Clarence Beam",
            "An antenna that concentrates signals in one direction",
            "An antenna that reverses the phase of received signals",
        ],
        "correct": 2,
        "explanation": "A beam antenna (like a Yagi) focuses radiated power in a specific direction, providing gain over an isotropic radiator. Typical beamwidth for a 3-element Yagi is about 60 degrees.",
    },
    {
        "id": "T9B01",
        "exam": "fcc_tech",
        "section": "T9",
        "text": "Why is it important to have a low SWR in an antenna system that uses coaxial cable feed line?",
        "choices": [
            "To reduce television interference",
            "To maximize power transfer from the transmitter to the antenna",
            "To minimize signal bandwidth",
            "To increase receiver selectivity",
        ],
        "correct": 1,
        "explanation": "A low Standing Wave Ratio (ideally 1:1) means the antenna impedance closely matches the feed line impedance, maximizing power transfer and minimizing reflected power and feed line losses.",
    },
    # ── FCC Technician (T0 -- AC power circuits, RF hazards, safety) ──
    {
        "id": "T0A01",
        "exam": "fcc_tech",
        "section": "T0",
        "text": "Which of the following is a safety hazard of a 12-volt storage battery?",
        "choices": [
            "Touching both terminals with the hands can cause electrical shock",
            "Shorting the terminals can cause burns, fire, or an explosion",
            "RF emissions from the battery",
            "Lead poisoning from the battery terminals",
        ],
        "correct": 1,
        "explanation": "A car battery can deliver hundreds of amperes into a short circuit, causing extreme heat, fire, or hydrogen gas explosion. Always use fused connections.",
    },
    # ── FCC General (G1 -- Commission Rules) ──
    {
        "id": "G1A01",
        "exam": "fcc_general",
        "section": "G1",
        "text": "On which High Frequency (HF) bands does a General class license provide transmitting privileges on all amateur frequencies?",
        "choices": [
            "80, 40, 20, and 15 meters",
            "160, 60, 30, 17, 12, and 10 meters",
            "80, 40, 20, 15, and 10 meters",
            "All HF bands except portions of 160, 80, 40, 20, and 15 meters",
        ],
        "correct": 3,
        "explanation": "General class operators have privileges on all HF bands, but portions of certain bands (160m, 75m, 40m, 20m, 15m) are reserved for Extra class only. Generals have access to all of 10m, 12m, 17m, 30m, and 60m.",
    },
    {
        "id": "G1B01",
        "exam": "fcc_general",
        "section": "G1",
        "text": "What is the maximum transmitter power an amateur station may use on the 12 meter band?",
        "choices": [
            "50 watts PEP",
            "200 watts PEP",
            "1500 watts PEP",
            "1000 watts PEP",
        ],
        "correct": 2,
        "explanation": "FCC Part 97.313 limits amateur stations to 1500 watts PEP output on most HF bands, including 12 meters, unless a lower power limit applies to a specific sub-band or mode.",
    },
    {
        "id": "G1C01",
        "exam": "fcc_general",
        "section": "G1",
        "text": "What is the maximum bandwidth permitted by FCC rules for amateur radio stations operating on USB frequencies in the 60 meter band?",
        "choices": [
            "3 kHz",
            "6 kHz",
            "2.8 kHz",
            "15 kHz",
        ],
        "correct": 2,
        "explanation": "The 60 meter band (5 MHz) has special restrictions including a 2.8 kHz bandwidth limit for USB emissions, channelized operation on five specific frequencies, and 100W PEP ERP limit.",
    },
    # ── FCC General (G2 -- Operating Procedures) ──
    {
        "id": "G2A01",
        "exam": "fcc_general",
        "section": "G2",
        "text": "Which mode of voice communication is most commonly used on the high frequency amateur bands?",
        "choices": [
            "Frequency modulation (FM)",
            "Double sideband AM",
            "Upper sideband (USB) for frequencies above 10 MHz; lower sideband (LSB) below 10 MHz",
            "Pulse modulation",
        ],
        "correct": 2,
        "explanation": "By convention, USB is used on bands at 14 MHz and above (20m, 17m, 15m, 12m, 10m), while LSB is used on bands below 10 MHz (160m, 80m, 40m). 60m uses USB only.",
    },
    {
        "id": "G2B01",
        "exam": "fcc_general",
        "section": "G2",
        "text": "Which of the following is true concerning access to frequencies?",
        "choices": [
            "Nets always have priority",
            "No frequency will be assigned for the exclusive use of any station",
            "DX stations have priority on calling frequencies",
            "Contest operation has priority over normal operation",
        ],
        "correct": 1,
        "explanation": "FCC Part 97.101(b): 'Each station licensee and each control operator must cooperate in selecting transmitting channels and in making the most effective use of the amateur service frequencies. No frequency will be assigned for the exclusive use of any station.'",
    },
    {
        "id": "G2C01",
        "exam": "fcc_general",
        "section": "G2",
        "text": "Which of the following describes full break-in telegraphy (QSK)?",
        "choices": [
            "Breaking stations send the Morse code prosign BK",
            "Between words, the receiver mutes the speaker",
            "A timed relay switches the antenna between transmit and receive",
            "Transmit/receive switching allows reception between transmitted dots and dashes",
        ],
        "correct": 3,
        "explanation": "Full break-in (QSK) allows you to hear signals between the individual dots and dashes you are sending, not just between words. This requires fast T/R switching using PIN diodes or vacuum relays.",
    },
    # ── FCC General (G3 -- Radio Wave Propagation) ──
    {
        "id": "G3A01",
        "exam": "fcc_general",
        "section": "G3",
        "text": "What is the sunspot number?",
        "choices": [
            "A measure of solar activity based on counting sunspots and sunspot groups",
            "The number of sunspots visible on the Sun at any given hour",
            "The number of days since the last solar flare",
            "A prediction of the next solar maximum date",
        ],
        "correct": 0,
        "explanation": "The sunspot number (SSN) quantifies solar activity. Higher SSN means greater ionization of the F layer, enabling better HF propagation at higher frequencies. The 11-year solar cycle ranges from SSN ~0 (minimum) to ~200 (maximum).",
    },
    {
        "id": "G3B01",
        "exam": "fcc_general",
        "section": "G3",
        "text": "What is the maximum distance along the Earth's surface that is normally covered in one hop using the F2 region?",
        "choices": [
            "1000 miles (1600 km)",
            "2500 miles (4000 km)",
            "500 miles (800 km)",
            "7500 miles (12000 km)",
        ],
        "correct": 1,
        "explanation": "A single F2-layer hop can reach approximately 2500 miles (4000 km). Multi-hop propagation can span continents, with signals refracting between the ionosphere and ground multiple times.",
    },
    {
        "id": "G3C01",
        "exam": "fcc_general",
        "section": "G3",
        "text": "Which ionospheric layer is most responsible for long-distance HF communication on the 20 meter band during the day?",
        "choices": [
            "D layer",
            "E layer",
            "F1 layer",
            "F2 layer",
        ],
        "correct": 3,
        "explanation": "The F2 layer (200-400 km altitude) provides the most reliable long-distance HF propagation during daylight hours because it has the highest electron density and persists after sunset.",
    },
    # ── FCC General (G4 -- Amateur Radio Practices) ──
    {
        "id": "G4A01",
        "exam": "fcc_general",
        "section": "G4",
        "text": "What is the purpose of the 'notch filter' found on many HF transceivers?",
        "choices": [
            "To restrict the transmitter output to a specific range",
            "To reduce interference from a carrier or tone near the received frequency",
            "To eliminate RF interference on the power supply",
            "To filter out harmonics from the transmitter",
        ],
        "correct": 1,
        "explanation": "A notch filter is a very narrow band-reject filter used in the receiver's audio or IF stage. It removes a single interfering carrier or heterodyne without affecting the desired signal significantly.",
    },
    {
        "id": "G4B01",
        "exam": "fcc_general",
        "section": "G4",
        "text": "What item of test equipment contains horizontal and vertical channel amplifiers?",
        "choices": [
            "An ohmmeter",
            "A signal generator",
            "An oscilloscope",
            "A spectrum analyzer",
        ],
        "correct": 2,
        "explanation": "An oscilloscope uses horizontal (time-base) and vertical (voltage) amplifiers to display voltage versus time on a CRT or LCD screen, making it essential for waveform analysis.",
    },
    # ── FCC General (G5 -- Electrical Principles) ──
    {
        "id": "G5A01",
        "exam": "fcc_general",
        "section": "G5",
        "text": "What is impedance?",
        "choices": [
            "The opposition to the flow of current in an AC circuit",
            "The force that drives electrons through a conductor",
            "The electrical charge stored in a capacitor",
            "The rate at which electrical energy is used",
        ],
        "correct": 0,
        "explanation": "Impedance (Z, measured in ohms) is the total opposition to alternating current, combining resistance (R) and reactance (X): Z = R + jX. It varies with frequency.",
    },
    # ── FCC Extra (E1 -- Commission Rules) ──
    {
        "id": "E1A01",
        "exam": "fcc_extra",
        "section": "E1",
        "text": "On what amateur frequencies above the 2 meter band are spread spectrum transmissions permitted?",
        "choices": [
            "902-928 MHz, 2.390-2.450 GHz, 5.650-5.925 GHz, 10.0-10.5 GHz, and 24.0-24.25 GHz",
            "Only on the 1.2 GHz band",
            "All amateur bands above 2 meters",
            "220 MHz and 450 MHz bands only",
        ],
        "correct": 0,
        "explanation": "FCC Part 97.311 authorizes spread spectrum emissions on specific UHF and microwave amateur bands. The listed frequencies correspond to amateur bands at 33 cm, 13 cm, 5 cm, 3 cm, and 1.25 cm.",
    },
    {
        "id": "E1B01",
        "exam": "fcc_extra",
        "section": "E1",
        "text": "Which of the following constitutes a spurious emission?",
        "choices": [
            "An emission outside the signal's necessary bandwidth that can be reduced without affecting the information transmitted",
            "Any emission that exceeds the transmitter's rated power output",
            "An amateur station transmission made at random without the proper call sign identification",
            "An emission that causes harmful interference to another authorized station",
        ],
        "correct": 0,
        "explanation": "A spurious emission is any emission on a frequency outside the necessary bandwidth that can be reduced without affecting the information transfer. Includes harmonics, parasitic oscillations, and intermodulation products.",
    },
    # ── FCC Extra (E2 -- Operating Practices and Procedures) ──
    {
        "id": "E2A01",
        "exam": "fcc_extra",
        "section": "E2",
        "text": "What is the direction of an ascending pass for an amateur satellite?",
        "choices": [
            "From west to east",
            "From east to west",
            "From south to north",
            "From north to south",
        ],
        "correct": 2,
        "explanation": "Most amateur satellites are in polar or near-polar orbits. An ascending pass means the satellite is traveling south to north (moving toward the North Pole). A descending pass goes north to south.",
    },
    {
        "id": "E2B01",
        "exam": "fcc_extra",
        "section": "E2",
        "text": "In digital television, what does a 'null packet' contain?",
        "choices": [
            "Packet that takes no data to fill a required frame",
            "Packet containing encryption keys",
            "Packet with error correction codes",
            "Packet that identifies the stream type",
        ],
        "correct": 0,
        "explanation": "Null packets are filler packets used in MPEG transport streams (such as ATSC/DVB amateur television) to maintain constant bit rate when no actual data needs to be sent.",
    },
    # ── FCC Extra (E3 -- Radio Wave Propagation) ──
    {
        "id": "E3A01",
        "exam": "fcc_extra",
        "section": "E3",
        "text": "What is the cause of the short-term variability in the Earth's magnetic field known as a 'sudden ionospheric disturbance' (SID)?",
        "choices": [
            "Solar flares emitting X-rays that increase D-layer absorption",
            "Coronal holes emitting high-speed solar wind streams",
            "Geomagnetic storms caused by CME impacts",
            "Sprites and elves in the mesosphere",
        ],
        "correct": 0,
        "explanation": "Solar flares emit X-rays that travel at the speed of light and reach Earth in ~8 minutes, causing immediate enhanced ionization of the D layer. This increases absorption of HF signals on the sunlit side of Earth -- a Sudden Ionospheric Disturbance (SID), also known as a Dellinger fade.",
    },
    # ── Canadian ROC-M Marine Radio ──
    {
        "id": "ROCM01",
        "exam": "roc_m",
        "section": "REG",
        "text": "What authority issues the Restricted Operator Certificate (Maritime) in Canada?",
        "choices": [
            "Transport Canada",
            "Innovation, Science and Economic Development Canada (ISED)",
            "Canadian Coast Guard",
            "Fisheries and Oceans Canada",
        ],
        "correct": 1,
        "explanation": "Innovation, Science and Economic Development Canada (ISED, formerly Industry Canada) issues the ROC-M. It is required for operating VHF marine radios in Canadian waters.",
    },
    {
        "id": "ROCM02",
        "exam": "roc_m",
        "section": "CH",
        "text": "What VHF channel is designated as the international distress, safety, and calling channel?",
        "choices": [
            "Channel 9",
            "Channel 13",
            "Channel 16",
            "Channel 22A",
        ],
        "correct": 2,
        "explanation": "Channel 16 (156.800 MHz) is the international VHF distress, safety, and calling frequency. All vessels must monitor Channel 16 when underway unless actively communicating on another channel.",
    },
    {
        "id": "ROCM03",
        "exam": "roc_m",
        "section": "OP",
        "text": "What is the correct format for a MAYDAY distress call?",
        "choices": [
            "MAYDAY spoken once, followed by vessel name",
            "MAYDAY MAYDAY MAYDAY -- vessel name repeated once",
            "MAYDAY MAYDAY MAYDAY -- THIS IS -- vessel name repeated three times",
            "MAYDAY spoken once, position given, nature of distress",
        ],
        "correct": 2,
        "explanation": "The correct distress call format: 'MAYDAY MAYDAY MAYDAY, THIS IS [vessel name] [vessel name] [vessel name]' (spoken three times each). Follow with position, nature of distress, assistance required, and number of persons on board.",
    },
    {
        "id": "ROCM04",
        "exam": "roc_m",
        "section": "DSC",
        "text": "What does a Maritime Mobile Service Identity (MMSI) number identify?",
        "choices": [
            "The type of cargo on board",
            "The vessel, coast station, or group of vessels for Digital Selective Calling",
            "The maximum speed of the vessel",
            "The insurance registration of the vessel",
        ],
        "correct": 1,
        "explanation": "An MMSI is a unique 9-digit number that identifies a vessel, coast station, or group for Digital Selective Calling (DSC). It functions like a telephone number for automated radio calls.",
    },
    {
        "id": "ROCM05",
        "exam": "roc_m",
        "section": "GMDSS",
        "text": "Which system provides global maritime distress alerting using satellites?",
        "choices": [
            "GPS",
            "AIS",
            "GMDSS (Global Maritime Distress and Safety System)",
            "LORAN-C",
        ],
        "correct": 2,
        "explanation": "The GMDSS integrates satellite and terrestrial radio systems including INMARSAT, COSPAS-SARSAT (EPIRBs), NAVTEX, and DSC to provide automated distress alerting and safety information worldwide.",
    },
]


def get_questions(exam: str = "fcc_tech", count: int = 10) -> list[dict[str, Any]]:
    """Return a random selection of questions for a given exam."""
    pool = [q for q in EXAM_QUESTIONS if q["exam"] == exam]
    if len(pool) <= count:
        return pool
    return random.sample(pool, count)


def grade_exam(
    answers: list[tuple[str, int]],
) -> dict[str, Any]:
    """
    Grade a set of answers. answers is a list of (question_id, chosen_index).
    Returns dict with score (correct/total), percentage, and per-question results.
    """
    correct_count = 0
    results: list[dict[str, Any]] = []

    question_map: dict[str, dict[str, Any]] = {q["id"]: q for q in EXAM_QUESTIONS}

    for qid, chosen in answers:
        q = question_map.get(qid)
        if q is None:
            results.append({
                "id": qid,
                "chosen": chosen,
                "correct_answer": None,
                "is_correct": False,
                "explanation": "Unknown question ID",
            })
            continue
        is_correct = chosen == q["correct"]
        if is_correct:
            correct_count += 1
        results.append({
            "id": qid,
            "text": q["text"],
            "chosen": chosen,
            "chosen_text": q["choices"][chosen] if 0 <= chosen < len(q["choices"]) else "invalid",
            "correct_answer": q["correct"],
            "correct_text": q["choices"][q["correct"]],
            "is_correct": is_correct,
            "explanation": q["explanation"],
        })

    total = len(answers)
    return {
        "correct": correct_count,
        "total": total,
        "percentage": round(correct_count / total * 100, 1) if total > 0 else 0.0,
        "passed": (correct_count / total >= 0.70) if total > 0 else False,
        "results": results,
    }


def exam_sections(exam: str) -> list[str]:
    """Return distinct section names for an exam."""
    return sorted({q["section"] for q in EXAM_QUESTIONS if q["exam"] == exam})


def questions_for(exam: str, section: str | None = None) -> list[dict[str, Any]]:
    """Return questions filtered by exam and optional section."""
    result = [q for q in EXAM_QUESTIONS if q["exam"] == exam]
    if section is not None:
        result = [q for q in result if q["section"] == section]
    return result


def exam_list() -> list[str]:
    """Return list of available exam names."""
    return sorted({q["exam"] for q in EXAM_QUESTIONS})
