---
name: electronics-expert
description: Use when working with electronic design, circuit analysis, PCB layout, SPICE simulation, BOM management, component selection, signal integrity, power electronics, embedded systems hardware, or any electronics engineering concern. Covers analog/digital circuit design, simulation, PCB manufacturing, component sourcing, and industry standards. Trigger keywords: electronics, circuit, PCB, SPICE, BOM, schematic, KiCad, Eagle, Altium, LTspice, ngspice, resistor, capacitor, inductor, transistor, MOSFET, op-amp, microcontroller, FPGA, signal integrity, EMI, power supply, voltage, current, impedance, Gerber, pick-and-place, footprint, datasheet, component, soldering, reflow, oscilloscope.
---

# Electronics Expert

This skill is the complete, self-contained electronics engineering knowledge base
for gludd agents. Every section is written to be executable, accurate, and
comprehensive. No external references needed -- the skill IS the knowledge.

---

## 1. Electronic Design Fundamentals

### 1.1 Ohm's Law and Basic DC Analysis

Ohm's Law is the most frequently used equation in electronics. Internalize all
three forms:

    V = I x R      (voltage = current x resistance)
    I = V / R      (current = voltage / resistance)
    R = V / I      (resistance = voltage / current)

Units: Voltage (V) in volts, Current (I) in amperes (subcircuit currents in mA
or microamps), Resistance (R) in ohms (practical range from milliohms for shunts
to megaohms for bias networks).

Power in resistive loads:

    P = V x I = I^2 x R = V^2 / R

A resistor's power rating must exceed the calculated dissipation by at least 2x
for reliability (derating). A 1/4W resistor carrying 0.2W will run hot and drift
-- use 1/2W.

Series resistors:

    R_total = R1 + R2 + ... + Rn
    I is the same through all
    V across each = I x R_n  (voltage divider)

Parallel resistors:

    1/R_total = 1/R1 + 1/R2 + ... + 1/Rn
    R_total = (R1 x R2) / (R1 + R2)   (two resistors, most common case)
    I_total splits inversely with R

Voltage divider (two resistors in series):

    V_out = V_in x R2 / (R1 + R2)

Where R2 is the resistor to ground. This is the most common subcircuit in analog
design -- it biases transistors, sets op-amp references, and scales ADC inputs.
Always account for the load impedance: if R_load is comparable to R2, use R2' =
R2 || R_load in the formula, or buffer with an op-amp voltage follower.

Current divider (two resistors in parallel):

    I_R1 = I_total x R2 / (R1 + R2)
    I_R2 = I_total x R1 / (R1 + R2)

Current takes the path of least resistance, split proportionally.

### 1.2 Kirchhoff's Laws

KVL (Kirchhoff's Voltage Law): The algebraic sum of voltages around any closed
loop is zero. In practice: sum of voltage drops across components equals the sum
of source voltages. Walk a loop and add voltage rises (going from - to + through
a source) and subtract voltage drops (going from + to - through a load) -- the
total must be zero.

KCL (Kirchhoff's Current Law): The algebraic sum of currents entering a node
equals zero. Current entering = current leaving. At any junction, what flows in
must flow out. This is the basis for nodal analysis.

Mesh analysis: Define loop currents (clockwise by convention), write KVL for
each mesh, solve the system of linear equations. Use when the circuit has more
loops than nodes -- 3 loops, 2 nodes -> mesh is faster.

Nodal analysis: Define node voltages (reference one node as ground, 0V), write
KCL for each non-reference node, solve. Use when there are more nodes than loops
-- 2 non-reference nodes, 3 loops -> nodal is faster. This is what SPICE uses
internally (modified nodal analysis, MNA).

### 1.3 Thevenin and Norton Equivalents

Any linear two-terminal network can be reduced to:

Thevenin: A voltage source V_th in series with a resistance R_th.
- V_th = open-circuit voltage at the terminals
- R_th = equivalent resistance looking into the terminals with all independent
  sources zeroed (voltage sources -> short, current sources -> open)

Norton: A current source I_n in parallel with a resistance R_n.
- I_n = short-circuit current at the terminals
- R_n = R_th (same value)

Conversion between Thevenin and Norton:

    V_th = I_n x R_th
    I_n = V_th / R_th

Maximum power transfer theorem: Maximum power is delivered to a load when
R_load = R_th. At this point, efficiency is exactly 50% (half the power is
dissipated in R_th). For power supplies, you want R_load >> R_th (voltage source
behavior); for current sources, R_load << R_th.

### 1.4 Superposition

In a linear circuit with multiple independent sources, the response at any point
is the sum of the responses caused by each source acting alone, with all other
independent sources zeroed.

Procedure:
1. Zero all but one independent source (V -> short, I -> open).
2. Solve for the quantity of interest.
3. Repeat for each independent source.
4. Sum the partial results.

Superposition does NOT apply to power (P = I^2 x R is nonlinear). Compute
voltage or current first, then compute power from the total.

### 1.5 Impedance and Frequency Response

Impedance (Z) is the complex generalization of resistance for AC circuits:

    Z = R + jX

Where R is resistance (real part, dissipates power) and X is reactance
(imaginary part, stores energy). j is the imaginary unit. Electronics uses j
instead of i to avoid confusion with current.

Resistor: Z_R = R (purely real, frequency independent -- ideal; real resistors
have parasitic inductance and capacitance that matter above ~100MHz).

Capacitor: Z_C = 1 / (j*omega*C) = -j / (omega*C)
- omega = 2*pi*f (angular frequency, rad/s)
- Phase: current leads voltage by 90 degrees (ICE: I before E in a Capacitor)
- At DC (f=0): open circuit (infinite impedance)
- At infinite frequency: short circuit (zero impedance)
- Reactance magnitude: X_C = 1/(2*pi*f*C)
- Corner frequency of an RC filter: f_c = 1/(2*pi*R*C)

Inductor: Z_L = j*omega*L
- Phase: voltage leads current by 90 degrees (ELI: E before I in an Inductor)
- At DC (f=0): short circuit (zero impedance)
- At infinite frequency: open circuit (infinite impedance)
- Reactance magnitude: X_L = 2*pi*f*L
- Corner frequency of an RL filter: f_c = R/(2*pi*L)

Impedance in series: Z_total = Z1 + Z2 + ... + Zn (complex addition)
Impedance in parallel: 1/Z_total = 1/Z1 + 1/Z2 + ... + 1/Zn

Magnitude: |Z| = sqrt(R^2 + X^2)
Phase angle: theta = arctan(X/R)

RLC series resonance (band-pass filter):
- Resonant frequency: f_0 = 1/(2*pi*sqrt(L*C))
- At resonance, X_L = X_C, they cancel. Impedance = R (minimum).
- Quality factor: Q = (1/R) x sqrt(L/C) = f_0 / BW
- Bandwidth: BW = f_0 / Q = R/(2*pi*L)
- At resonance, voltage across L or C is Q x V_source. A Q of 100 means 100V
  across the inductor for a 1V input -- this can destroy components.

RLC parallel resonance (band-stop / notch filter):
- Same resonant frequency: f_0 = 1/(2*pi*sqrt(L*C))
- At resonance, impedance is maximum (ideally infinite, R_parallel in practice).
- Parallel RLC is the anti-resonance: the tank circuit blocks f_0.

### 1.6 Time Constants and Transient Response

RC circuit (resistor + capacitor):
- Time constant: tau = RC (seconds)
- Charging: V_C(t) = V_final x (1 - e^(-t/tau))
- Discharging: V_C(t) = V_initial x e^(-t/tau)
- After 1*tau: 63.2% of final value
- After 3*tau: 95.0%
- After 5*tau: 99.3% (considered fully settled for most purposes)
- The current decays exponentially: I(t) = (V/R) x e^(-t/tau)

RL circuit (resistor + inductor):
- Time constant: tau = L/R (seconds)
- Current buildup: I(t) = I_final x (1 - e^(-t*R/L))
- Current decay: I(t) = I_initial x e^(-t*R/L)
- Voltage across inductor: V_L(t) = V x e^(-t*R/L) -- the inductive kickback

RLC circuit (damped second-order):
- Natural frequency: omega_n = 1/sqrt(L*C)
- Damping factor (series): zeta = R/(2) x sqrt(C/L)
- Damping factor (parallel): zeta = (1/(2*R)) x sqrt(L/C)
- zeta < 1: underdamped (ringing, overshoot)
- zeta = 1: critically damped (fastest settling without overshoot)
- zeta > 1: overdamped (slow, no overshoot)
- Peak overshoot: exp(-pi*zeta/sqrt(1-zeta^2))
- Settling time (to 2%): 4/(zeta*omega_n)

### 1.7 Filters

Passive filters (R, L, C only -- no gain):

First-order RC low-pass:

    f_c = 1/(2*pi*R*C)
    |H(f)| = 1/sqrt(1 + (f/f_c)^2)
    Phase shift at f_c: -45 degrees
    Rolloff: -20 dB/decade (-6 dB/octave) above f_c

First-order RC high-pass:

    f_c = 1/(2*pi*R*C)
    |H(f)| = (f/f_c)/sqrt(1 + (f/f_c)^2)
    Phase shift at f_c: +45 degrees
    Rolloff: -20 dB/decade below f_c
    C is in series with the input; R is the output to ground.

Active filters (op-amp based, can provide gain):

Sallen-Key topology: two-pole (second-order) low-pass or high-pass with a single
op-amp. Gain set by a resistive divider. Q factor is set by the ratio of the two
resistors (for unity gain: R1=R2, C1=2xC2 gives Q=0.5; C1=C2 gives Q=0.5
critical). Easy to build, limited Q range at higher gains.

Multiple Feedback (MFB) topology: inverting, two-pole. Better stop-band rejection
than Sallen-Key but inverts. Good for high-Q band-pass. Component values are less
sensitive to tolerance than Sallen-Key.

Filter approximations (normalized prototypes):

| Type | Passband | Stopband | Phase | Use When |
|------|----------|----------|-------|----------|
| Butterworth | Maximally flat, no ripple | Moderate rolloff | Moderate nonlinearity | General-purpose, audio crossovers, anti-aliasing (flat passband means no amplitude distortion) |
| Chebyshev Type I | Ripple (specified in dB, typically 0.1-1dB) | Steeper than Butterworth | More nonlinear phase | Steep cutoff needed, passband ripple is tolerable |
| Chebyshev Type II | Flat | Ripple (inverse Chebyshev) | Similar to Type I | Steep cutoff but need flat passband |
| Bessel (Thompson) | Gradual rolloff | Poor | Maximally linear (constant group delay) | Time-domain applications: pulse/step response, oscilloscope front-ends, digital data filters -- preserves waveform shape |
| Elliptic (Cauer) | Ripple in both | Steepest possible | Worst phase | Maximum selectivity; anti-aliasing with tight transition bands |

Filter order selection:
- 1st order: -20 dB/decade, simple, passive or active, no overshoot.
- 2nd order: -40 dB/decade, the standard building block (Sallen-Key, MFB).
- 4th order: -80 dB/decade, two 2nd-order stages cascaded. Good for anti-aliasing
  before an ADC (need -80dB at Nyquist from the passband edge).
- 8th order: -160 dB/decade, switched-capacitor ICs (LTC1064, MAX7400) or
  precision analog. Used in spectrum analyzers and instrumentation.

Bode plots:
- Magnitude plot: 20 x log10(|H(f)|) in dB vs log10(f).
- Phase plot: angle(H(f)) in degrees vs log10(f).
- A pole contributes -20 dB/decade rolloff and -90 degrees phase shift.
- A zero contributes +20 dB/decade and +90 degrees phase shift.
- The corner frequency is the -3dB point: |H(f_c)| = 1/sqrt(2) = 0.707 = -3.01 dB.

Decade vs octave:
- Decade: 10x frequency (e.g., 1kHz to 10kHz). 20 dB/decade = 6 dB/octave.
- Octave: 2x frequency (e.g., 1kHz to 2kHz). 6 dB/octave = 20 dB/decade.

### 1.8 Noise in Electronic Circuits

Johnson-Nyquist thermal noise:

    V_n_rms = sqrt(4 x k_B x T x R x delta_f)

- k_B = 1.380649 x 10^-23 J/K (Boltzmann constant)
- T = temperature in Kelvin (300K at room temp)
- R = resistance in ohms
- delta_f = bandwidth in Hz

A 50-ohm resistor at room temperature produces ~0.9 nV/sqrt(Hz). A 1M-ohm
resistor produces ~130 nV/sqrt(Hz). This is why high-impedance nodes are noisy --
keep resistances low in sensitive front-ends.

Shot noise: caused by discrete charge carriers crossing a potential barrier:

    I_n_rms = sqrt(2 x q x I_DC x delta_f)

- q = 1.602 x 10^-19 C (electron charge)
- I_DC = DC current through the junction

Shot noise is white (constant spectral density). In a BJT, the base current shot
noise is I_nb = sqrt(2 x q x I_B x delta_f); collector shot noise is I_nc =
sqrt(2 x q x I_C x delta_f). Shot noise dominates in photodiodes and low-current
circuits.

Flicker noise (1/f noise, pink noise): spectral density proportional to 1/f.
Caused by traps and defects in semiconductors. The corner frequency f_c (where
1/f noise equals thermal noise) is a figure of merit:
- BJT: typically 100Hz-10kHz
- JFET: 50Hz-1kHz
- MOSFET: 10kHz-1MHz (MOSFETs are noisier at low frequencies)
- Metal film resistor: essentially no 1/f noise
- Carbon composition: significant 1/f noise

Noise figure (NF):

    NF = 10 x log10(SNR_in / SNR_out)  [dB]

Measures how much an amplifier degrades SNR. An ideal amplifier has NF = 0 dB.
A low-noise amplifier (LNA) for RF will specify NF < 1 dB.

Noise floor in ADC systems: quantization noise of an ideal N-bit ADC:

    SNR = 6.02N + 1.76  [dB]

- 8-bit: ~50 dB
- 12-bit: ~74 dB
- 16-bit: ~98 dB
- 24-bit: ~146 dB (theoretical; practical is ~110-120 dB due to analog noise)

Total noise calculation: sum uncorrelated noise sources in quadrature (RMS):

    V_n_total = sqrt(V_n1^2 + V_n2^2 + ... + V_nk^2)

### 1.9 Ground Types and Grounding Strategy

Signal ground: The reference point for analog and digital signals. All voltage
measurements are relative to this node. In a schematic, this is the ground symbol
(downward-pointing triangle or three horizontal lines).

Power ground: Carries return currents from high-current paths (motor drivers,
relays, power stages). Power ground should have its own return path to the power
supply, separate from signal ground, to prevent power-stage current from
modulating the signal reference.

Chassis/earth ground: Connected to the metal enclosure and, through the AC mains
ground pin, to literal earth. Provides safety (fault current path) and EMI
shielding. The chassis ground is NOT the same as signal ground -- connecting them
directly creates a ground loop that picks up 50/60 Hz hum.

Analog vs digital ground:
- Analog ground (AGND): reference for ADCs, DACs, op-amps, sensors.
- Digital ground (DGND): reference for microcontrollers, logic gates, digital
  ICs. Digital return currents have high-frequency harmonics from fast edges --
  these couple into analog circuits if they share a return path.
- The ADC/digital interface IC typically has separate AGND and DGND pins. Connect
  them at a SINGLE point (a star ground), as close to the IC as possible.

Star ground: All ground returns radiate from a single physical point (the star
point). Each subsystem has its own return trace to the star. Advantage: no shared
impedance -- current from one subsystem cannot modulate the ground of another.
Disadvantage: many long traces; impractical for large boards with many ICs.

Ground plane: A continuous copper layer (usually one entire PCB layer) provides
the lowest-impedance return path. A ground plane:
- Minimizes loop area, reducing EMI radiation and susceptibility.
- Provides controlled-impedance transmission lines.
- Distributes heat.
- Return current follows the path of least inductance -- directly under the
  signal trace (at high frequencies). This is the image current principle.

Split planes: Separate analog and digital ground planes, connected at a single
bridge point under the ADC or mixed-signal IC. No traces cross the split -- a
trace crossing a split creates a large loop area (the return current must detour
to the bridge point). If a trace MUST cross a split, place a stitching capacitor
(10-100nF) across the gap at the crossing point to provide an AC return path.

Ground loops: When two grounded points are connected via multiple paths, ambient
magnetic fields induce a 50/60 Hz current in the loop. The voltage drop across
the loop impedance appears as hum in the signal. Solutions: break the loop (use
a single ground connection), use a differential input, or use an isolation
transformer/optocoupler.

### 1.10 Decoupling and Bypass Capacitors

A decoupling capacitor provides local energy storage for a digital IC, supplying
the transient current spikes when gates switch. Without decoupling, the current
spike must travel from the power supply through the PCB trace inductance, causing
a voltage droop at the power pins: delta_V = L_trace x dI/dt.

Capacitor impedance vs frequency:

    |Z| = sqrt(ESR^2 + (2*pi*f*ESL - 1/(2*pi*f*C))^2)

At low frequencies, capacitive (|Z| is proportional to 1/f). At the self-resonant
frequency (SRF), X_C = X_L. Above SRF, inductive (|Z| is proportional to f). The
capacitor acts as a capacitor only below SRF.

Multi-value decoupling strategy (parallel capacitors):

| Capacitor | Typical Value | Package | SRF (approx) | Effective Range |
|-----------|--------------|---------|-------------|-----------------|
| Bulk electrolytic/tantalum | 10-470 uF | Through-hole / large SMD | 1-100 kHz | DC to ~1 MHz (energy reservoir) |
| Ceramic MLCC | 10 uF | 1206/0805 | 1-5 MHz | 1 kHz - 10 MHz |
| Ceramic MLCC | 100 nF | 0603/0402 | 10-40 MHz | 1 MHz - 100 MHz (standard logic decoupling) |
| Ceramic MLCC | 1 nF | 0402 | 100-500 MHz | 50 MHz - 1 GHz |
| Ceramic MLCC | 100 pF | 0201 | 500 MHz - 2 GHz | 500 MHz+ (RF/ultra-high-speed) |

The rule of thumb -- one 100nF per power pin, plus bulk 10uF per IC or per group
of ICs -- works for most designs below 100MHz. ESL dominates above SRF, so ESL
is the real spec that matters at high frequency. Smaller packages (0402, 0201)
have lower ESL -- a 0402 capacitor has ~0.5nH ESL vs ~2nH for a 1206.

Placement: Place the smallest-value capacitor closest to the IC power pin (the
one that handles the highest frequency). Placement more than a few mm from the
pin adds trace inductance that defeats the purpose: a 1cm trace adds ~10nH,
which at 100MHz has impedance 2*pi*f*L = 6.3 ohms -- comparable to the
capacitor's ESR.

Anti-resonance: When two parallel capacitors of different values have their
SRFs far apart, the inductive region of the larger cap and the capacitive region
of the smaller cap can form a parallel LC resonance -- a high-impedance peak at
some intermediate frequency. Adding a small series resistor (0.5-2 ohms) with
the bulk capacitor damps this resonance. SPICE simulation with realistic ESL
(nH per mm of trace + component ESL from datasheet) reveals this.

### 1.11 Pull-Up and Pull-Down Resistors

A pull-up resistor connects a signal line to VCC; a pull-down connects it to
GND. They prevent floating inputs, which pick up noise and cause erratic
behavior (especially CMOS inputs -- a floating CMOS gate can oscillate and draw
excess current).

Value selection tradeoff:
- Too low (<1k ohm): wastes power, requires the driving device to sink/source
  significant current. At 3.3V, 1k pulls 3.3mA continuously.
- Too high (>100k ohm): susceptible to noise coupling. The input leakage current
  (typically +/-1 uA for CMOS) causes a voltage drop across the resistor: at
  100k, 1uA produces 100mV offset.
- Sweet spot: 4.7k-10k for general digital. 2.2k-4.7k for I2C.

I2C pull-up calculation:
The maximum pull-up is limited by the bus capacitance and rise time:

    R_pullup_max = t_rise / (0.8473 x C_bus)

For standard mode (100kHz, t_rise = 1000ns) with 100pF bus: R_max ~ 11.8k.
For fast mode (400kHz, t_rise = 300ns) with 100pF: R_max ~ 3.5k.
For fast-mode plus (1MHz, t_rise = 120ns) with 100pF: R_max ~ 1.4k.

The minimum pull-up is limited by the driver's sink current capability
(typically 3mA for I2C at 3.3V). For 3.3V and 3mA: R_min ~ 1.1k.

Common values: 4.7k for 100kHz, 2.2k for 400kHz. For long cables or large buses
(>200pF), use an I2C buffer (PCA9515, TCA9517) rather than pushing R_pullup
below the driver's minimum.

Open-drain / open-collector circuits: A MOSFET (open-drain) or BJT
(open-collector) can pull the line LOW but cannot drive it HIGH -- the pull-up
resistor handles the HIGH state. This is how I2C, 1-Wire, and wired-OR logic
buses work. A lower R_pullup gives faster rise time but higher power; a higher
R_pullup is lower power but slower and more susceptible to noise.

---## 2. Component Selection and BOM Management

### 2.1 Resistors

| Type | Tolerance | Tempco | Power | Noise | Use When |
|------|-----------|--------|-------|-------|----------|
| Carbon film | +/-5% | +/-250-500 ppm/C | 0.125-2W | Moderate (few uV/V) | General purpose, cost-sensitive, non-critical |
| Metal film | +/-0.1-1% | +/-15-100 ppm/C | 0.125-3W | Low (<0.1 uV/V) | Precision analog, dividers, gain-setting, low-noise |
| Wirewound | +/-0.01-5% | +/-3-20 ppm/C | 1W-100W+ | Lowest | High power, current sense, high precision; but: inductive (bad for RF) |
| Thick film SMD | +/-1-5% | +/-100-400 ppm/C | 0201-2512 | Moderate higher | General SMD, cost-effective |
| Thin film SMD | +/-0.05-1% | +/-10-50 ppm/C | 0201-2512 | Low | Precision SMD, analog front-ends, instrumentation |
| Metal foil | +/-0.005-0.01% | +/-0.2-2 ppm/C | 0.25-5W | Negligible | Ultra-precision (metrology, reference dividers); expensive |

E-series standard values:
- E12: 10, 12, 15, 18, 22, 27, 33, 39, 47, 56, 68, 82 (x10^n). +/-10%.
  12 values per decade. Use for non-critical: pull-ups, LEDs, generic.
- E24: adds 11, 13, 16, 20, 24, 30, 36, 43, 51, 62, 75, 91. +/-5%. 24/decade.
- E48: 48 values/decade. +/-2%.
- E96: 96 values/decade. +/-1%. The standard for precision analog. Includes
  values like 105, 115, 127, 140, 154, 169, 187, 205, 226, 249, 274, 301, 332,
  365, 402, 442, 487, 536, 590, 649, 715, 787, 866, 953 (and standard E24).
- E192: +/-0.5% and better. Use only when precision demands it.

Design for availability: Prefer E12 values for non-critical circuits. During
component shortages, common E12 values are restocked first. An E96 value may
have 52-week lead time while the nearest E12 value is in stock. When possible,
design gain-setting and divider networks to use E12 values with minimal error.

Power derating: Rated power applies at 70C ambient. Above 70C, derate linearly
to zero at the maximum operating temperature (typically 125-155C). For
reliability, derate to <=50% of rated power for commercial, <=30% for
industrial/military.

SMD package sizes:
- 0201 (0.6x0.3mm): smallest, 1/20W. RF and ultra-compact. Hand soldering:
  nearly impossible.
- 0402 (1.0x0.5mm): 1/16W. Standard for compact designs. Hand soldering: expert.
- 0603 (1.6x0.8mm): 1/10W. Very common. Hand soldering: doable with fine tip.
- 0805 (2.0x1.25mm): 1/8W. Hand soldering: easy. Good for prototyping.
- 1206 (3.2x1.6mm): 1/4W. Hand soldering: easiest SMD.
- 1210, 2010, 2512: higher power (0.5W-2W). Use for current sense, power.

Special resistor types:
- Current sense (shunt): Sub-milliohm to 100m ohm, typically 1% or better, low
  tempco (<50 ppm). Kelvin (4-wire) connection: two pads carry the current,
  two pads sense the voltage drop -- eliminates PCB trace resistance from
  measurement. Power rating = I_max^2 x R.
- Fusible: Designed to fail open under sustained overload without flame. When it
  fails, it STAYS open.
- High-voltage: Special construction prevents arcing. Rated for 500V-10kV+.
- Thermistor (NTC): Resistance decreases with temperature. Used for inrush
  current limiting and temperature sensing.
- Thermistor (PTC): Resistance increases sharply at the Curie temperature. Used
  as self-resetting fuses (polyfuse / resettable fuse).

### 2.2 Capacitors

Ceramic MLCC (Multi-Layer Ceramic Capacitor): The workhorse of modern
electronics. Layers of ceramic dielectric interleaved with metal electrodes.

Dielectric classes:
- C0G / NP0: Near-zero tempco (+/-30 ppm/C), no aging, no DC bias effect,
  low loss (high Q). Values up to ~100nF. Use for: filters, timing, VCOs,
  resonance tanks, precision analog. At any temperature and voltage, capacitance
  stays within +/-5% of nominal.
- X7R: +/-15% over -55C to +125C. Moderate DC bias derating (a 10V X7R cap at
  10V DC bias may have only 20-50% of its rated capacitance). Values up to ~47uF.
  Use for: decoupling, bypass, general-purpose. NOT for filters or timing.
- X5R: +/-15% over -55C to +85C. Similar to X7R but lower temp range. Slightly
  higher capacitance density. Use for: consumer-grade decoupling.
- Y5V / Z5U: -82% to +22% over -30C to +85C. Huge DC bias derating. Very high
  capacitance density but terrible stability. Use for: bulk decoupling where
  actual value doesn't matter much. Never use in a filter or timing circuit.

DC bias derating -- the trap: A 10uF 6.3V 0805 X7R capacitor at 5V DC bias may
have only 2-3uF effective capacitance. Manufacturers provide DC bias curves --
check them. Strategy: use a capacitor rated for at least 2x the applied DC
voltage, or use a larger package, or (for timing/filtering) use C0G/NP0 which
has essentially zero DC bias effect.

Microphonics / piezoelectric effect: X7R and X5R capacitors are piezoelectric --
mechanical vibration produces a voltage. In audio circuits, this appears as a
microphonic response: tapping the board produces a signal at the output. Use C0G
or film capacitors in audio signal paths.

Electrolytic aluminum:
- Polarized: must observe polarity (cathode marked with stripe). Reverse voltage
  destroys the oxide dielectric -- the capacitor vents (or explodes).
- High ESR (tens of milliohms to several ohms). This limits ripple current
  capability. ESR increases at low temperatures and with aging.
- Lifetime: highly temperature-dependent. Rule of thumb: every 10C decrease in
  operating temperature doubles lifetime. A 2000h@105C cap run at 55C lasts
  2^5 x 2000h ~ 64,000 hours (7.3 years).
- Use for: bulk energy storage, power supply filtering.

Tantalum:
- Low ESR, stable over temperature, long life.
- Failure mode: SHORT CIRCUIT with potential ignition. Voltage derating: use at
  <=50% of rated voltage (a 16V tantalum for a 5V rail).
- Use for: space-constrained designs where low ESR and stability are needed.

Film capacitors (polypropylene, polyester/PET):
- Excellent stability, low loss, no polarity, no piezoelectric effect.
- Polypropylene (PP): lowest dielectric absorption, best for audio and precision
  analog. Larger per uF.
- Polyester (PET / Mylar): smaller per uF, but higher dielectric absorption.
- Use for: audio signal path, precision filters, sample-and-hold, timing.

Supercapacitors (EDLC): Very high capacitance (0.1F to 5000F+), very low voltage
rating (2.5-2.7V per cell; series for higher voltage). Used for backup power
(RTC), energy harvesting buffer, peak power assist. High leakage current
(10-100uA typical). Balance resistors needed when cells are in series.

Capacitor selection quick reference:

| Application | Preferred Type | Key Specs |
|-------------|---------------|-----------|
| Digital decoupling | X7R MLCC 100nF 0402 + 10uF 0805 | Frequency vs impedance (SRF/ESL) |
| Analog decoupling | X7R MLCC + 1-10uF tantalum/polymer | Low noise, stable |
| Audio signal coupling | Polypropylene film or C0G MLCC | No distortion, no microphonics |
| Filter timing element | C0G/NP0 MLCC or polypropylene film | Stability (tempco, aging, DC bias) |
| SMPS output filter | Aluminum electrolytic (low ESR) + ceramic | Ripple current rating, ESR |
| SMPS input bulk | Aluminum electrolytic | Ripple current, voltage rating |
| RF coupling / DC block | C0G MLCC | Low insertion loss at frequency |
| Crystal load caps | C0G/NP0 MLCC | Precision (correct C_load, stable) |
| Sample-and-hold | Polypropylene or C0G | Low dielectric absorption |
| Power backup (RTC) | Supercapacitor or electrolytic | Leakage current, capacity |

Voltage derating guidelines:
- Ceramic MLCC (X7R/X5R): rated >= 2x working voltage.
- Tantalum: rated >= 3x working voltage.
- Aluminum electrolytic: rated >= 1.2x working voltage (1.5x for reliability).
- Film: rated >= 1.5x working voltage.

### 2.3 Inductors and Ferrites

Inductor core materials:

| Core | Saturation B_sat | Frequency Range | Permeability | Use |
|------|-----------------|-----------------|-------------|-----|
| Air core | Never saturates | DC to GHz | 1 | RF, VHF/UHF, high-frequency filters. No core losses but large size for given L. |
| Iron powder | 0.5-1.5 T | DC - 100 MHz | 10-100 | SMPS output inductors, EMI filters. Distributed air gap gives soft saturation. |
| Ferrite (MnZn) | 0.3-0.5 T | DC - 10 MHz | 1000-15000 | Transformers, common-mode chokes, low-frequency EMI. High mu, high loss at RF. |
| Ferrite (NiZn) | 0.2-0.4 T | 10 MHz - 1 GHz | 10-1500 | Ferrite beads, RF chokes. Lower mu, much lower loss at high frequency. |
| Sendust / Kool Mu | 0.8-1.0 T | DC - 10 MHz | 26-125 | High-DC-bias inductors. Distributed air gap, low loss. More expensive. |
| Amorphous / nanocrystalline | 1.2-1.5 T | DC - 100 kHz | Very high | High-efficiency transformers (low core loss). Premium SMPS. |

Key inductor parameters:
- DCR (DC Resistance): The copper winding resistance. I^2R losses = I_rms^2 x
  DCR. Keep DCR low for efficiency; low-DCR inductors are larger and cost more.
- SRF (Self-Resonant Frequency): Where parasitic capacitance resonates with L.
  Above SRF, the inductor behaves as a capacitor. Use inductors with SRF at
  least 3-5x above the operating frequency.
- I_sat (Saturation Current): The DC current where inductance drops to a
  specified percentage (usually 70-80%) of the nominal value. In a buck
  converter, saturation causes a sudden current spike.
- I_rms (RMS Current Rating): Determined by the temperature rise from copper
  losses. Limited by the wire gauge and thermal resistance.

Ferrite beads: A lossy inductor designed to dissipate high-frequency energy as
heat. Specified by impedance at a given frequency (e.g., 120 ohms @ 100MHz).
The impedance is almost all resistive (R) at the specified frequency. Selection:
- Target the noise frequency: a bead rated 120 ohms @ 100MHz may be only a few
  ohms at 10MHz. Check the impedance vs frequency curve.
- Current rating: impedance drops as DC current increases (core bias effect).
  At rated current, impedance may be 25-50% of the zero-bias value.
- Placement: as close to the noise source as possible. Series on the power line,
  followed by a decoupling capacitor to ground (forms an LC low-pass).

Common-mode chokes: Two windings on a single core, phased so that differential
current cancels (zero net flux -- core does not saturate) but common-mode current
adds. Used on power input lines, USB data lines, and Ethernet to suppress
common-mode EMI without affecting the differential signal.

Inductor selection for SMPS (buck converter):

    L_min = (V_in - V_out) x V_out / (V_in x f_sw x delta_I_L x I_out_max)

Where delta_I_L = ripple current as fraction of I_out_max (typically 0.2-0.4).
Larger L -> lower ripple current -> lower output ripple voltage but slower
transient response. Smaller L -> faster transient response but higher ripple and
higher core/conduction losses.

### 2.4 Semiconductors

Diodes:

Schottky diode: Vf ~ 0.15-0.45V (vs 0.6-0.7V for silicon PN). Lower Vf = lower
conduction losses. Fast recovery (no minority carrier storage -- majority-
carrier device). No reverse recovery charge (Qrr). Tradeoff: higher reverse
leakage current (uA to mA, vs nA for silicon). Leakage doubles every ~10-25C.
Common parts: 1N5817/18/19 (1A, 20/30/40V), BAT54 (200mA, 30V, SOT-23), SS14
(1A, 40V, SMA).

Zener diode: Operates in reverse breakdown at a sharply defined voltage.
- Zener (Vz < 5.6V): true Zener breakdown (quantum tunneling). Negative tempco.
- Avalanche (Vz > 5.6V): carrier multiplication. Positive tempco.
- At Vz ~ 5.6V, the two effects cancel -- near-zero tempco. The 5.6V zener is
  the sweet spot for voltage references.
- For precision reference: use a bandgap reference IC (TL431: 2.5V +/-1%, 0.4 ohm
  dynamic impedance) or a dedicated voltage reference (REF30xx, ADR45xx, LM4040).

TVS (Transient Voltage Suppressor) diode: Designed to absorb ESD and surge
transients. Key specs: standoff voltage V_RWM (must be > normal operating
voltage), breakdown voltage V_BR, clamping voltage V_C at peak pulse current
I_PP, peak pulse power P_PP (8/20us waveform). Placement: as close to the
connector as possible (<5mm trace). Common parts: USBLC6-2 (USB 2.0), SRV05-4
(USB/Ethernet), SMAJ/SMBJ/SMCJ series (400W-1500W).

LED (Light Emitting Diode): Forward voltage Vf depends on color:
- Infrared: ~1.2-1.5V
- Red: 1.8-2.0V
- Yellow/Amber: 2.0-2.2V
- Green: 2.0-3.1V
- Blue/White: 3.0-3.4V
- UV: 3.1-4.5V

Current limiting resistor: R = (V_supply - Vf) / I_led. For 20mA indicator LED
at 5V: R = (5 - 2) / 0.02 = 150 ohms. PWM dimming is preferred over analog
current reduction (no color shift at low currents). PWM frequency > 200Hz to
avoid visible flicker (>2kHz for camera visibility).

BJTs: NPN vs PNP: NPN current flows collector-to-emitter when base is pulled
high (V_be > 0.6V). PNP current flows emitter-to-collector when base is pulled
low (V_eb > 0.6V). NPN is more common, cheaper, and faster.

Operating regions:
- Cutoff: V_be < ~0.6V. I_c ~ 0. Transistor is OFF.
- Active: V_be > 0.6V, V_ce > V_ce(sat). I_c = hFE x I_b. Transistor amplifies.
- Saturation: I_b > I_c / hFE. V_ce drops to V_ce(sat) ~ 0.1-0.3V. Fully ON.

Switching speed: Turn-off (storage time): minority carriers in the base must
recombine. A base resistor to ground (B-E resistor, ~10k) helps drain charge.
For faster turn-off: use a Baker clamp (Schottky diode from base to collector)
to prevent deep saturation, or use a MOSFET instead. At high frequencies, a BJT
used as a saturated switch is slow -- use a MOSFET for switching above ~100kHz.

hFE (DC current gain, beta): I_c / I_b. Varies hugely with I_c, temperature,
and unit-to-unit. Design so the circuit works with the minimum specified hFE.
For switching, force I_b = I_c / 10 to I_c / 20 to guarantee saturation.

Common small-signal BJTs: 2N3904 (NPN) / 2N3906 (PNP): general purpose, 200mA,
40V, 300MHz fT. BC547 (NPN) / BC557 (PNP): similar, European. 2N2222 (NPN):
800mA, 40V. MMBT3904 / MMBT3906: SMD versions (SOT-23).

MOSFET (Enhancement-mode, most common): Normally OFF -- zero gate voltage -> no
channel. N-channel: positive V_gs turns on. P-channel: negative V_gs turns on.
Voltage-controlled device: gate draws essentially zero DC current.

Key parameters:
- V_gs(th): Minimum V_gs where the MOSFET starts to conduct (typically at
  I_d = 250uA). Logic-level MOSFETs: V_gs(th) < 1.5V, fully on at 3.3V or 5V.
  Standard MOSFETs may need 10V gate drive. R_ds(on) is specified at a particular
  V_gs; at lower V_gs, R_ds(on) increases sharply.
- R_ds(on): Drain-to-source resistance when fully on. Typically milliohms.
  Conduction losses = I_d^2 x R_ds(on). Positive tempco means MOSFETs can be
  paralleled and will share current.
- Q_g (Total gate charge): Charge that must be delivered to switch fully on.
  Switching loss = Q_g x V_gs x f_sw. Q_gd (Miller charge) is dominant.
- V_ds(max): Drain-source breakdown voltage. Must exceed peak drain voltage by
  >20% for reliability.
- Body diode: Intrinsic PN junction between drain and source. Useful in
  half-bridge circuits but reverse recovery charge (Q_rr) causes loss and EMI.

Gate drive considerations: The gate looks like a capacitor C_iss = C_gs + C_gd.
Peak gate current: I_gate_peak = V_drive / R_gate. External R_g: 1-22 ohms to
control switching speed (reduce EMI ringing). During the Miller plateau, the
driver current charges C_gd (C_rss). A strong gate driver (1-4A peak) shortens
the plateau.

Common power MOSFETs: N-channel 30V: IRFZ44N, IRLZ44N (logic level), SI2302
(SOT-23), AO3400 (SOT-23). N-channel 60-100V: IRF540, IRFZ44N. N-channel HV
(500-650V): IRF740, STP8NK80. P-channel: IRF9540, AO3401 (SOT-23), SI2301.

Op-Amps: Ideal op-amp assumptions (Golden Rules):
1. Infinite open-loop gain -> in closed-loop feedback, V_in+ = V_in- (virtual short).
2. Infinite input impedance -> zero current flows into the inputs.
3. Zero output impedance.

Real op-amp non-idealities:

| Parameter | Precision (e.g. OPA277) | General Purpose (e.g. LM358) | Effect |
|-----------|------------------------|------------------------------|--------|
| V_os (input offset) | 5-50 uV | 0.5-5 mV | DC error at output = V_os x closed-loop gain |
| I_b (input bias) | 0.1-10 pA (FET/CMOS) | 10-500 nA (BJT) | Voltage drop = I_b x R_source |
| CMRR | 120-140 dB | 70-100 dB | Rejects common-mode; output error = delta_V_cm / CMRR |
| PSRR | 120-140 dB | 70-100 dB | Ripple attenuation; output error = delta_V_ps / PSRR |
| Slew rate | 1-20 V/us | 0.3-0.5 V/us | Limits dV/dt; for sine: SR > 2*pi*f*V_peak |
| GBWP | 1-50 MHz | 0.5-10 MHz | For non-inverting: BW = GBWP / G. At G=100, 1MHz GBWP -> BW=10kHz |
| Rail-to-rail input | Yes (modern CMOS) | No (LM358 needs 1.5-2V below V+) | Input must include ground for single-supply |
| Rail-to-rail output | Yes (modern) | No (LM358: max out ~ V+ - 1.5V) | Output swing limited |

Common op-amp topologies:

Inverting amplifier:

    Gain = -Rf / R_in
    Input impedance = R_in  (loads the source)
    V_out = -V_in x (Rf / R_in)

Non-inverting input is grounded (or reference voltage). Use when you need a
virtual ground mixer (summing amplifier) or gain less than 1.

Non-inverting amplifier:

    Gain = 1 + Rf / R1  (R1 to ground)
    Input impedance = op-amp input impedance (very high, ~10^12 ohms for FET)
    V_out = V_in x (1 + Rf / R1)

Use when you need high input impedance (buffer) or gain >= 1.

Differential amplifier (single op-amp):

    V_out = (V2 - V1) x Rf/R1  (when R1=R2, Rf=Rg, matched)

Limited CMRR unless resistors are precision-matched (0.1% or better). For high
CMRR, use an instrumentation amplifier.

Instrumentation amplifier (three op-amps): Two non-inverting buffers (high input
Z) feed a differential stage. High CMRR (>100 dB) independent of resistor
matching. Gain set by a single external resistor R_gain: G = 1 + (2*R1/R_gain).
Use for: bridge sensors (strain gauges, load cells), thermocouples, ECG/EEG.
Classic IC: AD620, INA128.

Integrator (inverting):

    V_out(t) = -(1/RC) x integral of V_in(t) dt

Feedback capacitor instead of resistor. At DC, the capacitor is an open circuit
-- any DC offset integrates to saturation. Add a large feedback resistor
(100k-1M) in parallel with C to limit DC gain.

Differentiator (inverting):

    V_out(t) = -RC x d(V_in)/dt

Amplifies high-frequency noise -- prone to oscillation. Add a small resistor in
series with the input capacitor to limit high-frequency gain.

Comparator with hysteresis (Schmitt trigger): A comparator without hysteresis
will oscillate when the input is near the threshold. Hysteresis: the threshold
depends on whether the output is high or low.

    V_th_high = V_ref x (1 + R1/R2) - V_ol x (R1/R2)   (output low -> high)
    V_th_low = V_ref x (1 + R1/R2) - V_oh x (R1/R2)    (output high -> low)
    Hysteresis = V_th_high - V_th_low

Use a dedicated comparator IC (LM393, LM339) rather than an op-amp for fast,
clean switching.

Single-supply op-amp design: Bias the non-inverting input to mid-supply (Vcc/2)
using a resistor divider; AC-couple the input through a capacitor. Or use an
op-amp rated for single-supply with rail-to-rail input/output (MCP6001, OPA340).

### 2.5 Connectors

Key selection criteria:
- Pitch (pin spacing): Common: 2.54mm (0.1 inch), 2.00mm, 1.27mm, 1.00mm,
  0.50mm (fine-pitch FPC/FFC).
- Current rating per contact: Must exceed maximum current per wire with derating
  for temperature.
- Mating cycles: USB: 1500-5000. Board-to-board mezzanine: 30-100. FPC/FFC (ZIF):
  10-30. Test points: thousands.
- Environmental sealing (IP rating): IP20: no water protection. IP54: dust
  protected, splash resistant. IP67: dust-tight, immersion to 1m for 30 min.
- Locking mechanism: None (friction), latch/clip, screw-locking (D-sub), bayonet
  (BNC, circular MIL), push-pull (LEMO).

Common connector families:
- Molex / KK / SL series: 2.54mm pitch, wire-to-board. 1-5A per contact.
- JST: XH (2.5mm, general-purpose), PH (2.0mm, compact), SH (1.0mm, ultra-
  compact), VH (3.96mm, high-current). The specific series matters.
- TE Connectivity / AMP: AMPMODU (2.54mm board-to-board), Dynamic series (5-40A).
- Samtec: High-speed board-to-board (up to 112 Gbps PAM4). Mezzanine, edge rate.
- Phoenix Contact / Weidmuller: Terminal blocks (screw or spring clamp). Pitch:
  3.5mm, 5.0mm, 5.08mm. Used for power entry, field wiring, industrial.
- DIN 41612: Eurocard connector, 2.54mm pitch, 96-pin. Used in VMEbus, industrial
  backplanes.
- FPC/FFC: 0.5mm or 1.0mm pitch. ZIF connector. For display connections,
  cameras. Delicate -- not for repeated connections.
- RF connectors: SMA (DC-18GHz, 50 ohms), BNC (DC-4GHz, bayonet), SMB (snap-on),
  U.FL/IPEX (surface-mount, tiny, ~30 mating cycles).

### 2.6 BOM (Bill of Materials) Structure

A professional BOM is a machine-readable document for purchasing. Required columns:

| Column | Description | Example |
|--------|-------------|---------|
| Item # | Sequential line number | 1, 2, 3... |
| Qty | Quantity per assembly | 10 |
| Reference Designators | Comma-separated list | R1,R2,R3,R7,R12,R15 |
| Value | Component value/description | 10k +/-1% 0.1W |
| Manufacturer | Company name | Yageo |
| Manufacturer PN | Exact part number | RC0603FR-0710KL |
| Vendor | Distributor name | DigiKey, Mouser |
| Vendor PN | Distributor part number | 311-10.0KHRCT-ND |
| Package / Footprint | PCB land pattern | 0603 (1608 Metric) |
| Tolerance | As applicable | +/-1% |
| Voltage Rating | As applicable (caps, diodes) | 50V |
| Power Rating | As applicable (resistors) | 100mW |
| Temperature Coefficient | As applicable | +/-100 ppm/C |
| Dielectric | For capacitors | X7R, C0G/NP0 |
| Alternative PN | Second-source | RC1608F103CS (Samsung) |
| Lifecycle Status | Active / NRND / EOL / Obsolete | Active |
| Unit Cost @ Qty | Price at target volume | $0.0043 @ 1000pcs |
| Lead Time | Weeks | 4 weeks |
| RoHS Compliant | Yes/No | Yes |
| REACH Compliant | Yes/No | Yes |
| Notes | Any special instructions | Do Not Substitute (DNP) |

DNP (Do Not Populate): Components on the schematic/PCB that are not assembled.
Marked DNP in the BOM Notes. Common DNPs: alternate value for tuning, optional
filtering, test points, configuration resistors. All DNPs should have a
justification in the Notes column.

Lifecycle status:
- Active: In production, recommended for new designs.
- NRND (Not Recommended for New Designs): Still available, but manufacturer
  plans to discontinue. Don't use in new designs.
- EOL (End of Life): Last-time-buy announced. Place final order now.
- Obsolete: No longer manufactured. Must redesign.

Second-sourcing: Every critical component should have at least one alternate
manufacturer part number. Verify pin-compatibility by comparing datasheet pin
tables.

Cost optimization:
- Prefer common packages: 0603 resistor is cheapest and most available.
- Prefer standard (E12/E24) values: 4.7k is pennies; 4.87k (E96) may be special-
  order at low volume.
- Consolidate: Use the same part for multiple designators when possible. Every
  unique line item adds purchasing overhead.
- Watch supply chain: During MLCC shortages, 100nF 0402 and 10uF 0805 X7R are
  universally in demand. Design with alternative values if possible.
- LCSC for Asia-sourced parts: If manufacturing in China, LCSC (via EasyEDA) has
  lower prices and faster delivery for common parts.

---## 3. SPICE Simulation

### 3.1 Netlist Fundamentals

A SPICE deck is a plain-text file listing every component, its connection nodes,
and its value/model. Each line defines one component:

    COMMENT: * This is a comment (asterisk in column 1)
    RESISTOR: Rname n+ n- value
    CAPACITOR: Cname n+ n- value [IC=initial_voltage]
    INDUCTOR: Lname n+ n- value [IC=initial_current]
    DIODE: Dname anode cathode model_name
    BJT: Qname collector base emitter [substrate] model_name
    MOSFET: Mname drain gate source body model_name
    VOLTAGE SOURCE: Vname n+ n- [DC value] [AC mag phase] [TRAN type]
    CURRENT SOURCE: Iname n+ n- [DC value] [AC mag phase] [TRAN type]
    SUBCIRCUIT CALL: Xname n1 n2 ... nN subcircuit_name

Node numbering: Node 0 (zero) is always ground. All other nodes are positive
integers or alphanumeric names. A floating node (no DC path to ground) causes
a singular matrix error -- add a large resistor (1G ohm) to ground.

Voltage sources:

    DC: V1 VCC 0 DC 5V
    AC (for .ac analysis): V2 in 0 AC 1 0      (1V amplitude, 0 degree phase)
    SIN (transient sine): V3 sig 0 SIN(0 1 1kHz 0 0 0)
      = SIN(offset amplitude freq delay damping phase_delay)
    PULSE: V4 clk 0 PULSE(0 3.3 0 1n 1n 50u 100u)
      = PULSE(V_low V_high t_delay t_rise t_fall t_width t_period)
    PWL (piecewise linear): V5 sw 0 PWL(0 0 1m 0 1.001m 5 2m 5 2.001m 0)
      = PWL(time1 val1 time2 val2 ...)

Dependent sources:

    VCVS: Ename n+ n- nc+ nc- gain
    VCCS: Gname n+ n- nc+ nc- transconductance
    CCCS: Fname n+ n- V_sense gain  (V_sense is a 0V voltage source to sense current)
    CCVS: Hname n+ n- V_sense transresistance

### 3.2 Analysis Types

.op (Operating Point): DC solution. Finds all node voltages and branch currents
with capacitors open and inductors shorted. Every other analysis starts from this.

.dc (DC Sweep):

    .dc V_source_name V_start V_stop V_increment [source2 V_start2 V_stop2 V_inc2]
    .dc TEMP start stop increment   (sweep temperature)
    .dc PARAM param_name start stop increment   (parameter sweep)

Use for: transistor V-I curves, transfer function, DC operating range analysis.

.ac (AC Frequency Sweep):

    .ac DEC points_per_decade f_start f_stop
    .ac OCT points_per_octave f_start f_stop
    .ac LIN total_points f_start f_stop

All independent sources with an AC value become sinusoidal sources at the swept
frequency. The circuit is linearized around the DC operating point. This is why
.ac gives Bode plots but does NOT show clipping or slew-rate limiting.

Use for: filter frequency response, amplifier bandwidth, phase margin (Bode plot
of loop gain), PSRR vs frequency.

.tran (Transient Analysis):

    .tran T_step T_stop [T_start_for_output] [T_max_step]

Solves the full nonlinear differential equations over time. Most computationally
expensive. Step size is determined dynamically.

Practical guidelines:
- Set T_max_step to 1/100 of the fastest signal period (or 1/10 of the fastest
  edge) for good waveform fidelity. For a 100kHz SMPS with 10ns edges:
  T_max_step = 1ns.
- Add UIC flag: .tran 1n 10m 0 1n UIC skips the initial DC operating point.
- Save only needed nodes: .save V(out) V(in) I(Rload) to limit output.

Use for: startup transient, load step response, switching waveforms, oscillator
startup, PLL lock profile, audio amplifier THD analysis.

.noise (Noise Analysis):

    .noise V(output_node [, ref_node]) source_name DEC/OCT/LIN points f_start f_stop

Computes total output noise spectral density by summing contributions of every
noisy device.

.tf (Transfer Function): DC small-signal: .tf V(out) V_in. Returns gain, input
resistance, and output resistance at the DC operating point.

.step (Parameter Sweep):

    .step PARAM R_val LIST 1k 10k 100k
    .step PARAM R_val 1k 10k 1k   (linear: start stop increment)
    .step DEC PARAM C_val 1n 10u 5   (decade: 5 points per decade)
    .step TEMP LIST 0 25 85

Runs the entire analysis once for each step value. The waveform viewer shows
overlaid traces.

.meas (Measure Statements): Post-process simulation data to extract scalar values:

    .meas TRAN peak_current MAX I(Rload)
    .meas TRAN v_settle FIND V(out) WHEN V(out)=V_target CROSS=LAST
    .meas TRAN t_rise TRIG V(out) VAL=0.1*V_final RISE=1 TARG V(out) VAL=0.9*V_final RISE=1
    .meas AC bw WHEN V(out)=0.707*V_max       (find -3dB bandwidth)
    .meas AC phase_margin FIND V(out_phase) AT=bw

### 3.3 Models: .MODEL and .SUBCKT

.MODEL -- defines parameters for a primitive device type:

    .model D1N4148 D(Is=2.52n Rs=0.568 N=1.75 Cjo=4p M=0.4 tt=20n)

Primitive type codes: D (diode), NPN, PNP, NMOS, PMOS (four-terminal MOSFETs),
NJF, PJF (JFETs), VDMOS (vertical power MOSFETs -- LTspice-specific).

.SUBCKT -- hierarchical block with internal circuitry and external pins:

    .subckt OPAx84 1 2 3 4 5   ; (non-inv, inv, V+, V-, out)
    * ... internal netlist ...
    .ends OPAx84

Subcircuit instantiation:

    XU1 in+ in- VCC VEE out OPAx84

Vendor models: TI, Analog Devices, STMicro distribute SPICE models for their ICs
as .SUBCKT files. In LTspice: place the .subckt text on the schematic (as a SPICE
directive), create a symbol (.asy) with the same pin names. In ngspice: .include
opa277.lib then instantiate via X-call.

### 3.4 LTspice-Specific Features

Schematic capture: Draw the circuit; LTspice generates the netlist invisibly.

Behavioral sources (B-sources): Arbitrary voltage/current defined by expressions:

    B1 out 0 V=V(in)^2 + 3*sin(2*pi*1kHz*time)
    B2 load 0 I=if(V(ctrl)>0.6, V(out)/10, 0)

B-sources can reference other node voltages and currents, use functions (sin,
cos, exp, log, sqrt, abs, min, max, limit, if/else), and time.

Hierarchical design: Create a schematic for the subcircuit, create a symbol
(Hierarchy -> Create a New Symbol), place the symbol in the top-level schematic.

Third-party model import: Download the .lib/.sub file from the vendor. Add
.lib model_file.lib as a SPICE directive. For a standard part: right-click the
component -> Pick New Transistor/Diode/Op-Amp -> the model appears in the list.

### 3.5 ngspice

ngspice is the open-source SPICE implementation. Command-line tool (no built-in
GUI). Syntax is SPICE 3f5 compatible with extensions.

Running from command line:

    ngspice circuit.cir          # interactive mode
    ngspice -b circuit.cir        # batch mode (runs and exits)

XSPICE extensions: Event-driven digital simulation (gates, flip-flops, ADCs,
DACs) and code models (gain, summer, integrator, differentiator, limiter,
slew_rate, s_xfer for Laplace transfer functions, d_delay for digital delay).

KiCad + ngspice integration: KiCad 6.0+ has built-in SPICE simulation. Assign
SPICE models to schematic symbols (right-click -> Properties -> Simulation
Model). Run from the schematic editor (Inspect -> Simulator).

Limitations (ngspice vs LTspice): No built-in schematic capture (KiCad fills
this gap). No B-source equivalent with arbitrary expressions. Less vendor-
supplied models. VDMOS power MOSFET model is different. Much more limited
waveform viewer.

### 3.6 Common Simulation Scenarios

Power supply startup:

    .tran 10u 10m   ; 10us step, 10ms stop
    VIN VCC 0 PWL(0 0 1u 12)   ; ramp from 0 to 12V in 1us (soft start)
    .meas TRAN vout_final AVG V(out) FROM 8m TO 10m   ; steady-state voltage
    .meas TRAN overshoot MAX V(out) FROM 0 TO 5m

Load transient response:

    ILOAD out 0 PULSE(0.1 1 5m 1u 1u 5m 10m)
    ; = load steps from 100mA to 1A for 5ms, then back
    .tran 1u 15m
    .meas TRAN v_droop MIN V(out) FROM 5m TO 5.1m
    .meas TRAN v_overshoot MAX V(out) FROM 10m TO 10.1m

Op-amp stability (loop gain analysis): Break the feedback loop and inject an AC
signal. Insert a large inductor (1GH) in series with the feedback path (DC
short, AC open). Inject an AC source through a large capacitor (1kF) at the
summing node (DC open, AC short). Run .ac analysis. Plot V(feedback_node)/
V(inject_node). Phase at -3dB bandwidth = phase margin.

Filter frequency response:

    .ac DEC 50 1 1Meg
    VIN in 0 AC 1
    .meas AC f_c WHEN V(out)=0.707     ; -3dB cutoff
    .meas AC gain_dc FIND V(out) AT=10

Amplifier THD (Total Harmonic Distortion):

    .tran 1u 10m
    VIN in 0 SIN(0 1 1kHz)
    .tran 0 100m 90m 100n   ; skip first 90ms, simulate last 10ms (10 cycles)
    .four 1kHz V(out)
    .options plotwinsize=0   ; disable waveform compression (required for accurate FFT)

### 3.7 SPICE Model Limitations

SPICE models are approximations. Common gaps:

- Parasitics not included: A capacitor model is just C unless you add ESR, ESL,
  and leakage. An inductor model is just L unless you add DCR and parallel C.
- Thermal effects: SPICE doesn't model self-heating unless you explicitly build
  a thermal model.
- EMI and layout parasitics: SPICE assumes everything is lumped. Trace
  inductance, cross-coupling, and radiation are not captured.
- Manufacturing variation: .MODEL parameters are typical. Use .step to vary
  critical parameters.
- Subcircuit fidelity: A vendor's op-amp .SUBCKT may model macro behavior
  (GBWP, slew rate) but may not model every parameter. Cross-check simulation
  results against datasheet specs for the parameters you care about.

---## 4. PCB Design and Layout

### 4.1 PCB Stackup

A typical 4-layer board:

    Layer 1: TOP (signal, components) -- 1 oz (35um) copper
    Prepreg: 0.2mm FR-4 (epsilon_r ~ 4.0-4.6 @ 1MHz)
    Layer 2: GND (continuous ground plane) -- 0.5 oz (17um) copper
    Core: 0.5-1.5mm FR-4 (mechanical strength)
    Layer 3: PWR (power plane, or split power islands) -- 0.5 oz copper
    Prepreg: 0.2mm FR-4
    Layer 4: BOTTOM (signal, components) -- 1 oz copper

A typical 6-layer board:

    TOP (signal) -- small prepreg (~0.1mm, thin dielectric for tight coupling)
    GND (plane)
    INNER1 (signal, high-speed routing)
    INNER2 (signal, or additional power)
    PWR (plane)
    BOTTOM (signal)

The thin TOP->GND dielectric means microstrip traces can be narrower for the
same impedance, and the close ground plane reduces EMI.

Key stackup parameters for impedance control:
- Dielectric constant epsilon_r: FR-4 is nominally 4.2-4.6 @ 1GHz but varies
  batch-to-batch. For controlled impedance, your fab provides the actual
  epsilon_r for their specific laminate.
- Core vs prepreg: The core is a rigid sheet (fully cured FR-4 with copper foil
  on both sides). Prepreg is partially-cured sheet that bonds layers under heat.
  Cores have more consistent thickness than prepreg -- route impedance-critical
  traces on layers referencing a core.
- Copper thickness: 1 oz = 35um = 1.37 mils. 0.5 oz = 17um = 0.67 mils. Inner
  layers are typically 0.5 oz; outer layers are 1 oz (or plated up from 0.5 to
  1 oz).

Controlled impedance targets:

| Interface | Differential Z_diff | Single-Ended Z_0 |
|-----------|--------------------|-------------------|
| USB 2.0 (480 Mbps) | 90 ohm +/-15% | 45 ohm |
| USB 3.x / USB-C | 90 ohm +/-10% | -- |
| Ethernet (100BASE-TX) | 100 ohm +/-5% | -- |
| HDMI / DVI | 100 ohm +/-10% (TMDS) | 50 ohm |
| PCIe | 85-100 ohm +/-10% | -- |
| DDR memory | -- | 40-60 ohm (per controller spec) |
| CAN bus | -- | 120 ohm (termination) |
| RS-485 | -- | 120 ohm |
| 50 ohm RF (WiFi, BT, GPS, LoRa) | -- | 50 ohm (CPW common) |
| LVDS | 100 ohm +/-10% | -- |

### 4.2 Trace Width and Impedance (IPC-2221)

Current capacity (IPC-2221) for external layers, 1 oz copper:

    I = k x delta_T^0.44 x A^0.725

Where I = current (A), k = 0.048 for external/0.024 for internal, delta_T =
temperature rise above ambient (typically 10C), A = cross-sectional area (mil^2)
= trace width (mils) x copper thickness (mils).

Rule of thumb (external, 1 oz, 10C rise):
- 0.25mm (10 mil): ~1.0A
- 0.50mm (20 mil): ~1.8A
- 1.0mm (39 mil): ~3.2A
- 2.0mm (79 mil): ~6.0A

For high current, use wider traces, thicker copper (2-4 oz), and/or multiple
layers paralleled with stitching vias.

Microstrip impedance (outer layer trace over ground plane):

    Z_0 ~ (87 / sqrt(epsilon_r + 1.41)) x ln(5.98 x h / (0.8 x w + t))

Where h = dielectric height, w = trace width, t = trace thickness. For FR-4
(epsilon_r = 4.5), a 50 ohm trace on a 0.2mm dielectric: w ~ 0.35mm (14 mil).

Stripline impedance (inner layer trace between two planes):

    Z_0 ~ (60 / sqrt(epsilon_r)) x ln(1.9 x (2 x h + t) / (0.8 x w + t))

Use your PCB fabricator's impedance calculator -- they know their actual
epsilon_r and layer thicknesses.

### 4.3 Routing Rules

Differential pairs:
- Coupling: Route the two traces side-by-side with minimal spacing (typically
  equal to trace width). Consistent spacing along the full length.
- Length matching: USB: 50-150 mils; HDMI: 5-10 mils; PCIe Gen4: 5 mils.
  Mismatch = skew = differential signal converts to common-mode noise.
- Phase matching: Add serpentine bends to the SHORTER trace to match length.
  Place these near the source of mismatch, not at a random point.
- Impedance continuity: When changing layers, both signals transition together
  through matched vias with ground-stitching vias nearby.

Length matching (single-ended buses): DDR memory buses need data, address, and
control signals to arrive within a window (10-50 ps for DDR4). Use serpentine
routing. Serpentine design: spacing between segments >= 3x trace width, minimum
bend radius >= 3x trace width (45 degree bends, not 90), add serpentine close
to the pin needing lengthening.

Impedance discontinuities:
- Vias: A via adds ~0.5-1pF capacitance + ~0.5-1nH inductance. At >5GHz, a via
  can cause significant reflection. Use via stitching (ground vias near signal
  vias) for return current.
- Connectors: The connector impedance may not match trace impedance. For >1 Gbps,
  use connectors rated for the data rate.
- Layer transitions: When a trace moves from layer 1 to layer 3 (through a via),
  the return current must also transition. Place a ground via near the signal
  via to connect the two reference planes.

Via types:
- Through-hole via: Drilled through entire board, plated. Standard, cheap. For a
  0.3mm hole in 1.6mm board: ~0.5pF capacitance, ~1.3nH inductance.
- Blind via: Connects outer layer to inner layer, not through entire board. More
  expensive (sequential lamination). Reduces stub length.
- Buried via: Connects two inner layers, not visible from outer surfaces. Most
  expensive. Used in HDI.
- Microvia: Laser-drilled, very small (0.1mm or smaller), typically one layer
  deep. Used in HDI designs and BGA fanout.

Via stitching: Placing many ground vias along a path to create low-impedance
return current. Use along board edges (Faraday cage effect), along microstrip RF
traces (creates CPW structure), around high-speed digital ICs.

Thermal relief: A via or pad connected to a plane by thin spokes (thermal ties)
rather than full connection. Reduces heat sinking during soldering. Standard
spoke width: 0.25-0.5mm, 4 spokes at 45 degree angles.

### 4.4 Component Placement

1. Place connectors first -- fixed by the enclosure.
2. Place main ICs (microcontroller, FPGA, PMIC). Group supporting components
   around each IC.
3. Decoupling capacitors: As close as physically possible to each power pin.
   Trace from capacitor to IC pin should be <3mm and >0.25mm wide. Capacitor's
   other pad connects directly to ground plane (via directly beside pad).
4. Crystal / oscillator: Place as close as possible to XTAL_IN/XTAL_OUT pins.
   Keep traces <10mm, symmetrical, ground pour underneath. Load capacitors
   between crystal and IC, not on the far side of the crystal.
5. Analog / digital separation: Keep noisy digital traces (PWM, serial buses,
   high-speed memory) away from sensitive analog traces (sensor inputs, op-amp
   inputs, ADC references). Separate ground pours connected at a single point
   under the ADC or mixed-signal IC.
6. High-speed keepouts: No traces under RF antennas.
7. Thermal management: Power devices need copper area for heat spreading. Use
   copper pours on multiple layers connected with thermal vias. A 10x10mm
   copper area on 4-layer board provides ~50-100C/W theta_JA improvement.

### 4.5 PCB Design Rules

Clearance (track-to-track, track-to-pad, pad-to-pad):

| Voltage (DC or peak AC) | Minimum Clearance (External, Uncoated) |
|-------------------------|---------------------------------------|
| <15V | 0.15mm (6 mil) |
| 15-30V | 0.25mm (10 mil) |
| 30-50V | 0.5mm (20 mil) |
| 50-100V | 0.8mm (31 mil) |
| 100-150V | 1.5mm (60 mil) |
| 150-200V | 2.5mm (100 mil) |
| Mains (120-240VAC) | 3-4mm (with reinforced insulation) |

Trace width / spacing:
- Standard low-cost fab: 6/6 mil (trace/space) minimum.
- Mid-tier: 4/4 mil.
- Advanced: 3/3 mil.
- Ultra-HDI: <3/3 mil (laser-drilled microvias).

Annular ring: Minimum = (pad diameter - hole diameter) / 2. Typical: 0.15mm
(6 mil) for standard, 0.1mm (4 mil) for advanced. Design with annular ring
>=0.15mm for reliable fabrication.

Solder mask expansion: Gap between mask opening and copper pad. Standard: 0.1mm
(4 mil). For fine-pitch ICs (0.4-0.5mm pitch), use mask-defined pads or a
single mask opening covering all pads on a side.

Silkscreen: Line width >=0.15mm (6 mil), text height >=1.0mm. Keep silkscreen
off pads.

### 4.6 Manufacturing Outputs

Gerber files (RS-274X): One file per layer:
- Top copper (.GTL), Bottom copper (.GBL)
- Top solder mask (.GTS), Bottom solder mask (.GBS)
- Top silkscreen (.GTO), Bottom silkscreen (.GBO)
- Top paste (solder paste stencil) (.GTP), Bottom paste (.GBP)
- Board outline (.GKO or .GML)
- Drill drawing (.GDD or .TXT)

RS-274X vs X2: X2 embeds metadata (layer type, function, pad attributes) into
Gerber files. KiCad 6+ generates X2 by default.

Excellon drill files:
- Plated holes (.PTH or .drl)
- Non-plated holes (.NPTH)
- Specifies hole size, X/Y coordinate, and whether plated.

Pick-and-place file (centroid / PnP): CSV format: RefDes, X, Y, Rotation, Side
(Top/Bottom). The rotation must be correct -- a single rotated component causes
an entire board to fail.

IPC-2581 vs ODB++ vs Gerber comparison:
- Gerber: most basic, industry-standard, every fab accepts. No netlist/stackup.
- ODB++: layers + netlist + stackup + component data. Not all fabs accept.
- IPC-2581: open standard, XML, single-file, contains everything. The future but
  slow adoption.

DFM (Design for Manufacturing) checks:
- Acid traps: Acute angles (<90 degrees) in copper that trap etchant, causing
  over-etching. Use >=90 degree angles at T-junctions; add teardrops.
- Slivers: Thin copper fragments that can peel off. Minimum copper width >=0.15mm.
- Starved thermals: Thermal relief spokes too thin to carry current. Check spoke
  width x number of spokes meets current requirement.
- Solder mask slivers: Thin mask between fine-pitch pads. If <0.1mm, mask may
  peel. Remove the mask sliver entirely (one opening for multiple pads).
- Copper-to-edge clearance: Minimum 0.25-0.5mm (10-20 mil) from board outline.

Panelization:
- V-score: V-shaped grooves cut partially through board, allowing snap-apart.
  Good for rectangular boards. Cleaner edge than mouse-bites.
- Mouse-bites / tab routing: Small tabs with perforations. Used when V-score is
  impractical. Tabs leave rough spots needing filing.
- Fiducials: Small copper circles (1-2mm diameter) with clear mask opening, on
  panel rails. At least 3 per panel (corners), plus local fiducials near fine-
  pitch components.
- Tooling holes: Non-plated holes (2-4mm) in panel rails for fixturing.
- Rail clearance: Keep components and tracks 5-10mm away from panel edge.

### 4.7 EDA Tool Workflows

KiCad workflow:
1. Schematic (Eeschema): Draw circuit, assign footprints, run ERC.
2. Netlist: Eeschema -> Tools -> Update PCB from Schematic.
3. PCB layout (Pcbnew): Place components, route, add copper pours (zones). Run
   DRC iteratively.
4. DRC: Pcbnew -> Inspect -> Design Rules Checker. Must pass with zero errors.
5. Generate outputs: File -> Fabrication Outputs -> Gerbers + Drill Files. Use
   gerbview to visually inspect before sending to fab.
6. BOM + Pick-and-Place: File -> Fabrication Outputs -> BOM + Component Placement.

KiCad pro-grade features (6.0+): Length-tuned differential pair routing, custom
design rules, teardrop generation, 3D viewer with STEP models, zone manager,
net inspector with cross-probe.

Altium workflow: Schematic -> synchronize to PCB -> layout -> DRC -> outputs.
Output job files define Gerbers, NC drill, ODB++, pick-and-place, BOM, and
assembly drawings in one batch.

Free/OSS tools:
- KiCad (kicad.org): Fully capable open-source EDA. Version 6.0+ is stable,
  suitable for professional 2-8 layer designs up to several GHz.
- EasyEDA (easyeda.com): Browser-based with LCSC parts integration. Good for
  quick designs with Chinese component sourcing.
- LibrePCB (librepcb.org): Newer OSS EDA with built-in library manager. Simpler
  but less mature.
- Horizon EDA (horizon-eda.org): Modern OSS EDA with emphasis on library
  management and fast workflow.

---

## 5. Signal Integrity and EMI/EMC

### 5.1 Transmission Lines

When a trace is longer than lambda/10, it behaves as a transmission line --
voltage and current are functions of both position and time.

Critical length rule:

    L_critical = lambda / 10 = (c / (10 x f_max x sqrt(epsilon_r_eff)))

Where f_max = 0.35 / t_rise (the knee frequency). For 1ns rise time: f_max ~
350MHz. On FR-4 (epsilon_r_eff ~ 3.5 for microstrip), lambda ~ 0.46m ->
L_critical ~ 4.6cm. Traces longer than ~5cm with 1ns edges need transmission-
line treatment.

For a 100ps rise (common in FPGAs), L_critical ~ 5mm. Almost every trace on an
FPGA board is a transmission line.

Transmission line types:
- Microstrip: Trace on outer layer, ground plane on adjacent inner layer.
  Asymmetric dielectric. Most common for outer layers.
- Stripline: Trace sandwiched between two ground/power planes. Symmetric
  dielectric. Fully enclosed -> lower radiation, better isolation. Preferred for
  high-speed inner-layer routing.
- Coplanar waveguide (CPW): Trace with ground planes on either side in the SAME
  layer (plus ground plane below). Used for RF traces (WiFi, Bluetooth, GPS)
  because lateral ground provides additional isolation and allows easier 50 ohm
  matching.

Reflections: If the transmission line is not terminated in its characteristic
impedance, energy reflects back:

    Gamma (reflection coefficient) = (Z_load - Z_0) / (Z_load + Z_0)

- Gamma = 0: matched, no reflection.
- Gamma = -1: short circuit, full negative reflection.
- Gamma = +1: open circuit, full positive reflection (voltage doubles at open end).
- Z_load > Z_0: positive reflection (voltage overshoot).
- Z_load < Z_0: negative reflection (undershoot, potential ground bounce).

Termination strategies:
- Series termination (source): Resistor in series with driver, R_series =
  Z_0 - R_driver. Reflection absorbed at source on return trip. Simple, low
  power. Only for point-to-point.
- Parallel termination: Resistor to ground at receiver, R = Z_0. Simple but
  draws DC current.
- Thevenin termination: Two resistors (R1 to VCC, R2 to GND) where R1||R2 = Z_0
  and Thevenin voltage = receiver threshold. Lower power than parallel.
- AC termination: Resistor + capacitor to ground at receiver. R = Z_0, C blocks
  DC. No DC power but needs extra capacitor per trace.

### 5.2 Crosstalk

Crosstalk is unwanted coupling between adjacent traces through mutual capacitance
and mutual inductance.

- Near-end crosstalk (NEXT): Victim's near end (close to aggressor driver). In
  microstrip: dominated by capacitive coupling, same polarity as aggressor.
- Far-end crosstalk (FEXT): Victim's far end. In microstrip: difference between
  capacitive and inductive coupling. In stripline (homogeneous dielectric), they
  cancel -- FEXT ~ 0. Stripline has much lower crosstalk.

3W rule: Space parallel traces so center-to-center separation is at least 3x
the trace width. At 3W spacing, crosstalk is ~10-15%. At 5W, it drops to ~2-5%.
For sensitive analog traces or high-speed clocks, use >5W spacing.

Guard traces: A grounded trace between aggressor and victim. Can reduce
capacitive crosstalk but adds mutual inductance -- may INCREASE inductive
crosstalk. Guard traces help for capacitive-dominant coupling (high-impedance
victim) but are less effective (or harmful) for inductive-dominant coupling
(low-impedance victim). IF using a guard trace, it MUST be connected to ground
at BOTH ends and at regular intervals (every lambda/10). An ungrounded floating
guard trace acts as a coupling antenna.

### 5.3 High-Speed Design Principles

Rise time is the key parameter -- not clock frequency. A 1MHz clock with 1ns
rise time generates frequency components up to 350MHz. A 100MHz clock with 10ns
rise time generates components to 35MHz.

Bandwidth from rise time:

    f_knee = 0.35 / t_rise   (rule of thumb)
    f_knee = 0.5 / t_rise    (conservative, for design margin)

Critical length from rise time:

    L_crit = t_rise x c / (2 x sqrt(epsilon_r_eff))

DDR memory routing guidelines:
- Each byte lane (8 data bits + DQS strobe) must be length-matched within
  +/-10 mils (DDR3) to +/-5 mils (DDR4).
- DQS strobe must be length-matched to its data bits within +/-5-10 mils.
- Address/command/control lines must be length-matched to the clock.
- Route each byte lane on the same layer. Keep continuous ground plane under
  all DDR traces.
- Match trace impedance to controller spec (typically 40-50 ohm for DDR3,
  34-40 ohm for DDR4).
- Daisy-chain (address goes controller -> chip 1 -> chip 2) vs fly-by
  (address passes each chip with short stub). Fly-by is used in DIMMs.

### 5.4 Signal Integrity Simulation Tools

- HyperLynx (Mentor/Siemens): 2D/2.5D field solver + time-domain simulator.
  Industry standard for pre/post-layout SI.
- Sigrity (Cadence): Power-aware SI (PI+SI co-simulation). Models PDN impedance.
- ADS (Keysight): Full 3D EM simulation (Momentum, FEM). Used for RF/microwave
  SI (S-parameter extraction).

### 5.5 EMI/EMC Fundamentals

Near-field vs far-field:
- Near-field (< lambda/2*pi from source): E and H fields not coupled. Near-
  field probes used for diagnostic.
- Far-field (> lambda/2*pi): E and H coupled into propagating wave (E/H = 377
  ohms, impedance of free space). Compliance tests are in far-field.

Common-mode vs differential-mode noise:
- Differential-mode (DM): Current flows out on one conductor, returns on another.
  DM radiation proportional to loop area x current x frequency^2. Minimize loop
  area (tight return paths, ground plane).
- Common-mode (CM): Current flows same direction on multiple conductors, returns
  through ground/chassis/parasitic paths. CM radiation proportional to cable
  length x CM current x frequency. Even tiny CM current on long cable radiates
  significantly (cable is an antenna). Common-mode chokes minimize CM.

Conducted vs radiated emissions:
- Conducted: Noise on power/signal lines. Measured with LISN. Mitigated with
  input filters (common-mode choke, X and Y capacitors).
- Radiated: Noise through air. Measured with antenna at 3m/10m in anechoic
  chamber or OATS.

FCC Part 15 (USA):
- Class A (commercial/industrial): less strict.
- Class B (residential): stricter.
- Below 30MHz: conducted emissions (150kHz-30MHz).
- Above 30MHz: radiated emissions (30MHz-1GHz, up to 6GHz/40GHz for intentional
  radiators).

CISPR 32 / EN 55032 (International/Europe): Multimedia equipment. Limits:
30MHz-230MHz: 30 dBuV/m at 10m (Class B); 230MHz-1GHz: 37 dBuV/m.

Shielding effectiveness: SE (dB) = absorption loss + reflection loss.
- Aperture limit: Maximum opening dimension < lambda/10 (ideally lambda/20) at
  highest frequency. A 1cm slot is transparent at 3GHz.
- Seam conduction: Lid must be electrically bonded. Conductive gaskets (metal
  mesh, conductive elastomer) bridge gaps. Grounding fingers at regular intervals
  (every lambda/10).

Spread-spectrum clocking (SSC): Clock frequency modulated +/-0.5% to +/-2% at
30-33kHz rate. Spreads energy over wider bandwidth, reducing peak spectral
amplitude by 5-10 dB at the fundamental. Most modern MCUs/SoCs support SSC in
their PLL.

### 5.6 EMC Design Practices

1. Route high-speed traces on inner layers between planes (stripline -- fully
   enclosed, no direct radiation).
2. Never route high-speed signals across a split in the reference plane (return
   current must detour, creating large loop).
3. Stitch ground vias along board edges every 5-10mm (suppresses edge radiation).
4. Use ferrite beads on power inputs (series bead + decoupling caps = low-pass
   filter).
5. Use common-mode chokes on I/O cables (USB, Ethernet, CAN, RS-485). Any cable
   is an antenna.
6. Proper grounding topology:
   - Audio (<100kHz): single-point star ground. Shared impedance tolerable at
     low frequencies.
   - Digital (>1MHz): multi-point ground (ground plane). Return current follows
     least-inductance path directly under trace.
   - Mixed analog + digital: single-point connection between analog and digital
     ground planes at the ADC/DAC/mixed-signal IC.

---

## 6. Power Electronics

### 6.1 Linear Regulators

Operation: A control loop adjusts a pass transistor to maintain V_out. Power
dissipated: P_d = (V_in - V_out) x I_load + V_in x I_quiescent.

Efficiency: eta = V_out / V_in at maximum (ignoring quiescent current). 5V
output from 12V: eta = 5/12 = 42%. The difference -- 58% -- is wasted as heat.
Linear regulators are inefficient but simple and low-noise.

Key LDO specs:
- Dropout voltage V_do: Minimum V_in - V_out for regulation. PMOS pass LDO:
  100-500mV at rated current. NPN pass standard regulator: 1.5-2.5V.
- PSRR (Power Supply Rejection Ratio): Attenuation of input ripple at output in
  dB vs frequency. 60dB PSRR at 100kHz attenuates ripple by 1000x.
- Output noise: uV RMS over specified bandwidth (e.g., 10Hz-100kHz). For
  noise-sensitive analog (PLL/VCO, ADC reference): use LDO with <50 uV RMS noise
  or add external RC/LC filter after LDO.
- Quiescent current I_q: Regulator's own operating current. Battery-powered:
  need uA range (TI TPS7Axx: 5uA; Microchip MCP1700: 1.6uA).

Thermal design:

    T_j = T_ambient + P_d x theta_JA

For SOT-223 on 1 sq in copper: theta_JA ~ 50C/W. If P_d = 2W, T_j = 25 + 100 =
125C -- at max rating. Fix: reduce P_d (lower V_in), add heatsink, or use
switching regulator.

Protection features: Thermal shutdown (cuts off at ~165C, auto-recovers), current
limiting (foldback: as V_out drops, I_max decreases), reverse current protection
(parasitic body diode; some LDOs need external Schottky from output to input).

### 6.2 Switching Regulators

Buck Converter (Step-Down): A switch chops the input; LC filter smooths to DC.
Duty cycle D = V_out / V_in (ideal, CCM). Control loop adjusts D to regulate.

Inductor L:

    L_min = (V_in_max - V_out) x D_min / (delta_I_L x f_sw x 2)

where D_min = V_out / V_in_max, delta_I_L = ripple current (20-40% of
I_out_max). At 5V in, 3.3V out, 500kHz, 1A with 30% ripple: L ~ 15uH.

Output capacitor C_out:

    delta_V_out = delta_I_L x (ESR + 1/(8 x f_sw x C_out))

Two components: triangular from capacitive charging (1/(8fC)), square-wave from
ESR x delta_I_L. Low ESR is critical -- 100m ohm ESR with 0.3A ripple = 30mV
regardless of capacitance. Use ceramic MLCCs (ESR < 10m ohm). Also handle load
transient:

    C_out_min = delta_I_load x t_transient / delta_V_out_max

where t_transient ~ 2-3 switching cycles. For 500kHz, t_transient ~ 4-6us.

Input capacitor C_in: Input current is pulsed. RMS ripple current:

    I_cin_rms = I_out x sqrt(D x (1 - D))

Maximum at D=0.5: I_cin_rms = 0.5 x I_out. Capacitor ripple current rating must
exceed this. Use MLCCs for low ESR; add bulk electrolytic to damp input ringing
(input trace inductance + MLCCs = underdamped LC tank).

Catch diode (async) vs synchronous MOSFET:
- Asynchronous: Schottky diode from SW to GND. P_diode = Vf x I_out x (1 - D).
  Simple but Vf losses dominate at low V_out.
- Synchronous: Second MOSFET replaces diode. R_ds(on) x I^2 losses instead of
  Vf x I. Much lower loss at <5V out, >1A. Needs dead-time control for shoot-
  through prevention. Most modern bucks are synchronous.

Control modes:
- Voltage mode: Error amp directly controls PWM duty cycle. Simple but needs
  type-III compensation. LC filter double-pole complicates compensation.
- Current mode (peak/valley): Inner current loop controls inductor current;
  outer voltage loop sets current reference. Inductor becomes current source,
  reducing filter to single-pole (much easier compensation). Inherent cycle-by-
  cycle current limiting. Most modern bucks use this.
- Constant on-time (COT): Fixed on-time, variable off-time. Fast transient
  response, no compensation needed. Variable frequency. Used in TI TPS54xxx.

Compensation (current-mode): Type-II compensator (one zero, one pole at output
cap ESR zero) is usually sufficient. Zero cancels dominant pole phase lag; pole
at origin gives high DC gain. Feedback network: R1 V_out to FB, R2 FB to GND
(V_out = V_ref x (1 + R1/R2)), series RC from COMP to GND, optional feed-forward
cap across R1.

Boost Converter (Step-Up): Inductor charged from input (switch ON), dumps energy
to output through diode (switch OFF). V_out = V_in / (1 - D).

RHP zero -- the Achilles' heel of boost converters: When load increases, V_out
sags -> controller increases D. But increasing D initially REDUCES output current
(the inductor charges longer, delivers shorter -> average diode current drops).
Only after inductor current builds up does output recover. This is the wrong-way
RHP zero response.

    f_RHPZ = (1 - D)^2 x R_load / (2 x pi x L)

Crossover frequency must be well below f_RHPZ (f_c < f_RHPZ/3). This forces slow
transient response. For fast transients at high boost ratios, use SEPIC or
4-switch buck-boost.

SEPIC Converter: Can step up OR down. Uses two inductors (or coupled inductor)
and a series coupling capacitor. Advantages over boost: no RHP zero (with coupled
inductor, RHP zero is at high frequency), input current is continuous, output
less pulsed. Disadvantages: more components, slightly lower efficiency. Use when
input can be both above and below output (e.g., battery 2.7-4.2V -> 3.3V).

Flyback Converter: Isolated topology using coupled inductor (flyback transformer)
for galvanic isolation. Energy stored in core when primary switch ON, transferred
to secondary when OFF.

Key design:
- Turns ratio: N = N_s/N_p = V_out / (V_in_min x D_max/(1 - D_max))
  D_max typically <50% to prevent core saturation.
- Primary inductance: L_p = (V_in_min x D_max)^2 / (2 x P_out x f_sw x eta x
  K_rf) where K_rf ~ 0.5 for DCM.
- Transformer design: Gap the core to prevent saturation (flyback stores energy
  in air gap). AL_gapped = mu_0 x A_e / l_g (approximate). Primary turns:
  N_p = sqrt(L_p / AL_gapped).

Flyback vs forward: Flyback stores energy in core, transfers during OFF. Simpler,
<150W typical. Forward transfers during ON (true transformer), needs output
inductor, more efficient at >150W, needs reset winding or active clamp.

### 6.3 Thermal Design

Junction temperature:

    T_j = T_a + P_total x theta_JA

Where theta_JA = theta_JC (junction-to-case) + theta_CS (case-to-heatsink, with
thermal grease/pad) + theta_SA (heatsink-to-ambient).

Heatsink selection: Lower theta_SA = better. Forced air reduces theta_SA by
2-5x. Heatsink volume is the dominant factor.

Thermal vias: Array of small plated vias under power IC conducts heat from top
to inner/bottom layers. Typical: 0.3mm hole, 0.6mm pad, 1mm pitch, 5x5 array
under QFN/DFN thermal pad. Fill vias with solder during reflow for better
thermal conductivity.

Copper area: 1 sq in (645 mm^2) copper pour on top layer provides ~25-50C/W
theta_JA improvement. Use largest practical copper pour connected to IC thermal
pad.

SOA derating: Resistors derate to 0W at max temp. Derate to <=50% rated power
for long life. Semiconductors: check SOA curve; pulsed operation allows higher
current.

### 6.4 Protection Circuits

Overvoltage:
- TVS diode: Clamps transients (ESD, surge). <1ns response. Needs series
  impedance (fuse, PTC) to limit current through TVS.
- Crowbar circuit: SCR shorts power rail when overvoltage detected, blowing a
  fuse. Fast and definitive. Used on expensive downstream electronics.
- Zener clamp: Simple zener + series resistor. Only for low-energy transients.

Overcurrent:
- Fuse: One-time, manual replacement. Slow (ms-s for fast-blow). Rating ~ 1.25x
  I_max_continuous.
- PTC / polyfuse: Self-resetting. Resistance increases 100-1000x when tripped.
  Slower than fuse, higher cold resistance. Good for USB power, battery packs.
- eFuse: IC with MOSFET switch, current sensing, protection. Programmable limit,
  fast response, auto-retry or latch-off. TI TPS259xx, NIS5820.
- Current limiting in regulator: Cycle-by-cycle or foldback. Protects regulator
  itself but NOT a fuse replacement (regulator can fail short).

Reverse polarity protection:
- Series Schottky diode: Simple. Vf drop = power loss. Good for low-moderate
  current.
- P-channel MOSFET (reverse polarity): Gate to ground, source to input, drain to
  load. When polarity correct: V_gs = -V_in (ON). When reversed: V_gs > 0 (OFF).
  Near-zero voltage drop (R_ds(on) x I), no Vf loss. Preferred for >0.5A.
- Bridge rectifier: Allows either polarity input but has 2x Vf loss.

### 6.5 Battery Management

Li-Ion/Li-Po charge profiles:
- CC-CV (Constant Current - Constant Voltage): Charge at constant current (0.5C-
  1C, e.g., 1A for 2000mAh cell) until voltage reaches 4.20V. Then hold 4.20V
  while current tapers off. Terminate when current drops to C/10 or C/20.
- Never exceed 4.20V +/-1% (overcharge = fire risk).
- Charge temperature: 0 to 45C (below 0C, lithium plates out = permanent
  capacity loss + short circuit risk).
- Common charge ICs: TP4056 (1A, linear, simple), MCP73831 (500mA, SOT-23),
  BQ25890 (5A, switch-mode, USB PD).

Fuel gauging:
- Coulomb counting: Integrate current over time (Q = integral of I dt). Accurate
  but drifts over time -- needs periodic recalibration at full/empty.
- Voltage-based estimation: Measure open-circuit voltage (OCV), look up SoC from
  OCV-SoC table. Simple but inaccurate under load (I x R drop).
- Hybrid: Coulomb counting for dynamic range, OCV measurement at rest for drift
  correction. Used in TI BQ27427, Maxim MAX17048.

Protection:
- Undervoltage lockout (UVLO): Disconnect below ~2.5-3.0V/cell to prevent over-
  discharge damage.
- Overcharge: Disconnect above 4.25-4.30V/cell.
- Over-discharge: Disconnect below 2.0-2.5V/cell.
- Short circuit: Instant disconnect at high current (>5-10x rated).
- Common battery protection ICs: DW01 (single cell, with dual MOSFET), BQ29700,
  onboard PMIC protection in charger ICs.

Battery lifetime calculation example:
- Cell: 2000mAh, 3.7V nominal
- Load: 50mA average, 3.3V regulated
- Regulator efficiency: 85%
- Input power: P_out / eta = 3.3V x 0.05A / 0.85 = 0.194W
- Battery current: I_bat = 0.194W / 3.7V = 52.4mA
- Runtime: 2000mAh / 52.4mA = 38.2 hours (theoretical, derate for capacity
  variation with discharge rate and temperature)

---

## 7. Embedded Systems Hardware

### 7.1 Microcontrollers

ARM Cortex-M families:
- STM32 (STMicroelectronics): Broad portfolio (STM32F0/F1/F3/F4/F7/H7/L0/L4/G0/
  G4/U5/WB/WL). Cortex-M0+ to M7. Rich peripherals: multiple SPI/I2C/UART/CAN/
  USB/SDIO/TIM/PWM/ADC/DAC/DMA. CubeMX for pinout/clock configuration.
- RP2040 (Raspberry Pi): Dual Cortex-M0+ @133MHz, 264KB SRAM, PIO (Programmable
  I/O) -- unique feature for custom digital protocols. No internal flash (external
  QSPI). Pico board is $4 USD.
- nRF52 (Nordic): Cortex-M4F, integrated BLE 5.x, 2.4GHz radio. nRF52840:
  USB, NFC, Zigbee/Thread. Excellent for battery-powered wireless.
- ATSAMD (Microchip): Cortex-M0+ (SAMD21: Arduino Zero/MKR), Cortex-M4F (SAMD51:
  Adafruit Metro M4). Good peripheral set, Arduino-compatible ecosystem.

RISC-V:
- ESP32-C3 (Espressif): Single-core RISC-V @160MHz, WiFi 4 + BLE 5, 400KB SRAM.
- CH32V (WCH): RISC-V, ultra-low-cost. CH32V003: 10 cents, 2KB SRAM, 16KB flash.
  CH32V307: 144MHz, USB, CAN, Ethernet.
- GD32V (GigaDevice): RISC-V, compatible with STM32 pinouts.

AVR (8-bit):
- ATmega328P: Arduino Uno, 16MHz, 32KB flash, 2KB SRAM. Simple, reliable.
- ATtiny: Smaller (ATtiny85: 8-pin, 8KB flash; ATtiny1616: 20-pin, modern
  peripherals). Good for single-function embedded (sensor node, LED controller).

### 7.2 Debug Interfaces

SWD (Serial Wire Debug): ARM standard, 2 wires (SWDIO + SWCLK) plus optional
SWO (trace output). Connector: 0.05 inch pitch 10-pin Cortex debug header.
Debug probes: ST-Link (STM32), J-Link (Segger, universal), CMSIS-DAP (open
standard, works with pyOCD/OpenOCD), Black Magic Probe (open-source, GDB
directly over serial).

JTAG (Joint Test Action Group): 4-wire standard (TDI, TDO, TMS, TCK) plus
optional TRST. Used for FPGAs, larger MCUs, and boundary scan testing. Connector:
0.1 inch pitch 20-pin (ARM standard).

ICSP (In-Circuit Serial Programming): Microchip's interface for AVR/PIC. Uses
SPI-like pins (MISO, MOSI, SCK, RESET). AVR ISP mkII, USBasp, Arduino-as-ISP.

### 7.3 Common Peripherals

UART: Asynchronous serial, 2-wire (TX, RX), plus optional hardware flow control
(CTS, RTS). Voltage levels:
- TTL (3.3V/5V): Direct connection between MCUs. V_OH >= Vcc-0.6V, V_OL <= 0.4V.
- RS-232: +/-12V (mark = -12V, space = +12V). Requires level translator
  (MAX232 for 5V, MAX3232 for 3.3V).
- USB-UART bridges: CP2102 (Silicon Labs, common), CH340G (WCH, cheap), FT232
  (FTDI, most reliable, more expensive). All provide virtual COM port over USB.

Baud rates: Standard: 9600, 19200, 38400, 57600, 115200 (most common), 230400,
460800, 921600. Must match within 2-3% for reliable communication. Common clock
frequencies (16MHz, 8MHz) give good baud rate divisors for 115200.

I2C (Inter-Integrated Circuit): 2-wire (SDA data, SCL clock), open-drain with
pull-ups. Modes:
- Standard (Sm): 100kHz
- Fast (Fm): 400kHz
- Fast-mode Plus (Fm+): 1MHz
- High-speed (Hs): 3.4MHz

Addressing: 7-bit (most common, 112 usable addresses) or 10-bit (extended). Each
slave has a fixed address; check datasheet. Common address conflicts: multiple
identical sensors on same bus -- use chip with configurable address pins (ADDR
pin), or an I2C multiplexer (TCA9548A: 8 channels).

SPI (Serial Peripheral Interface): 4-wire full duplex (MOSI, MISO, SCK, CS).
Master generates SCK. Slave selected by CS (active low). Modes (0-3) determine
clock polarity (CPOL) and phase (CPHA). Mode 0 (CPOL=0, CPHA=0): data sampled
on rising edge -- most common. QSPI (Quad SPI): 4 data lines (IO0-IO3) for
faster flash access. Used for external flash on STM32, RP2040.

CAN (Controller Area Network): Differential bus (CAN_H, CAN_L). Terminated with
120 ohms at each end of bus. Dominant (0): CAN_H > CAN_L, recessive (1): both
~2.5V. Speeds: up to 1 Mbps (CAN 2.0), up to 8 Mbps (CAN FD). Message ID (11-bit
or 29-bit extended) determines arbitration priority (lower ID wins). Common
transceivers: MCP2551, SN65HVD230 (3.3V), TJA1050.

USB (Universal Serial Bus): Device vs Host vs OTG (On-The-Go). USB 2.0: 480 Mbps
(High Speed), 12 Mbps (Full Speed), 1.5 Mbps (Low Speed). D+ pulled to 3.3V via
1.5k resistor signals Full Speed; D- pulled up signals Low Speed. Enumeration:
host queries descriptors (device, configuration, interface, endpoint). Classes:
CDC (virtual COM port), HID (keyboard, mouse, gamepad), MSC (mass storage),
Audio, DFU (Device Firmware Upgrade). Common MCUs with USB device: STM32F103
(Full Speed), STM32F405 (High Speed), ATSAMD21 (Full Speed), RP2040 (Full Speed
via PIO).

### 7.4 Memory

External flash (SPI NOR): Winbond W25Q series: standard. W25Q32 (32 Mbit / 4MB),
W25Q128 (128 Mbit / 16MB). QSPI capable. Typical: 100,000 program/erase cycles,
20-year data retention. For higher capacity, use NAND flash (GB range) but needs
ECC and bad-block management.

EEPROM: I2C interface, byte-writable, 1 million+ write cycles. 24C02 (2Kbit /
256 bytes) to 24C512 (512Kbit / 64KB). Use for config data, calibration, device
serial numbers. Write cycle: 5ms (MCU must wait before next access).

FRAM (Ferroelectric RAM): I2C or SPI, essentially unlimited endurance (10^13-10^15
cycles), fast writes (no page buffer delays). Higher cost. Fujitsu MB85RC series,
Cypress FM24/FM25 series. Use for data logging, metering, applications needing
frequent writes.

SRAM vs PSRAM: SRAM (internal to MCU, fast), PSRAM (external, SPI/QSPI, 1-64MB,
slower but dense). ESP32 with integrated PSRAM: up to 8MB. RP2040: 264KB SRAM
only -- no external RAM interface without PIO bit-banging.

### 7.5 Clocking

Crystals: Fundamental mode vs overtone. For >30MHz: overtone crystals are common
(smaller, cheaper). Load capacitance C_L specified in datasheet:

    C_L = (C1 x C2)/(C1 + C2) + C_stray

Where C1 and C2 are external load capacitors, C_stray is PCB + pin capacitance
(~3-5pF). For C_L = 18pF: C1 = C2 = 2 x (C_L - C_stray) = 2 x (18 - 5) = 26pF
-> use 27pF standard. ESR (Equivalent Series Resistance): lower = easier to
start oscillating. Check the MCU's oscillator gain margin.

Oscillators: Integrated (4-pin can, VCC + GND + OUT + OE), no external caps
needed. TCXO (Temperature-Compensated Crystal Oscillator): +/-0.5 to +/-2.5 ppm
stability over temperature. Use for GPS (need accurate timing), cellular
modems, precision timekeeping.

PLL (Phase-Locked Loop): Multiplies reference clock to high frequencies. Input
8MHz crystal -> PLL -> 168MHz (STM32F4). Configuration: PLLM/N/P/Q dividers.
Check that VCO frequency stays within spec range (typically 100-432MHz for STM32).

### 7.6 Power for Embedded Systems

Low-power modes (Cortex-M):
- Sleep: CPU clock stopped, peripherals run. Wake in a few cycles.
- Deep Sleep / Stop: Most clocks stopped, SRAM retained. Wake in ~10us.
- Standby: SRAM lost (except backup registers), wake from RTC or external pin.
  Wake in ~100us. Current: ~1-2uA.
- Shutdown: Everything off except wake-up logic. Current: ~100-500nA. Wake
  source: external pin or RTC.

Wake-up sources:
- GPIO edge (rising/falling)
- RTC alarm (calendar time match)
- Independent watchdog (IWDG) or window watchdog (WWDG) timeout
- UART RX (first edge wakes, then UART peripheral needs clock to capture data)
- USB resume signaling

Battery lifetime calculation:
- Cell: 2000mAh Li-Po
- Device states: Active: 50mA for 1ms every 100ms (1% duty), Sleep: 100uA rest
  of time.
- Average current: (0.050A x 0.01) + (0.0001A x 0.99) = 0.5mA + 0.099mA = 0.6mA.
- Runtime: 2000mAh / 0.6mA = 3,333 hours = 138 days.
- Power gating: Turn off unused peripherals (UART, SPI, ADC) when idle. Each can
  save 0.1-1mA.

---

## 8. Measurement and Instrumentation

### 8.1 Oscilloscope

Bandwidth: BW >= 5x signal frequency for square waves (to capture 5th harmonic,
which gives reasonable edge fidelity). More accurately: rise time determines
needed bandwidth:

    BW (MHz) = 0.35 / t_rise (ns)

A 1ns rise time needs 350MHz scope BW to measure accurately. A 100MHz scope has
rise time = 0.35/100MHz = 3.5ns, so any signal edge faster than 3.5ns will be
displayed slower than real.

Sample rate: Nyquist requires >=2x bandwidth. Practice: 5-10x bandwidth for good
waveform fidelity. A 100MHz scope needs >=500MSa/s, preferably 1GSa/s.

Memory depth: record_length / sample_rate = capture time. 10M points at 1GSa/s
= 10ms capture. More memory = longer captures at high sample rate.

Probes:
- 1x vs 10x: 1x has full BW but loads circuit (1M ohm + ~100pF). 10x: 10M ohm
  + ~15pF -- much less loading, preferred for most measurements. BW limited by
  the probe (typical passive 10x: 100-500MHz).
- Differential probes: Measure across any two points (not ground-referenced).
  Essential for floating measurements (gate drive, high-side current sense,
  isolated circuits). Common: Tek P5200A, Micsig DP10013.
- Current probes: AC/DC, Hall effect + transformer. Tek TCP0030A (30A DC-120MHz).
  Rogowski coils for high frequency, limited low-frequency response.
- Active probes: FET input, very low capacitance (<1pF), high BW (>1GHz). Used
  for high-speed serial, DDR probing.

Triggering modes:
- Edge: Most common. Trigger on rising/falling edge at threshold.
- Pulse width: Trigger when pulse is shorter or longer than specified width.
  Find glitches.
- Runt: Trigger when pulse crosses one threshold but not the other (does not
  reach full logic level). Find timing/bus contention issues.
- Serial bus: Trigger on specific I2C address, SPI data pattern, UART byte.
  Time-correlates serial data with analog waveforms.

Key measurements:
- Vpp (peak-to-peak): Total voltage swing.
- Vrms (root mean square): For power/energy, equivalent DC value.
- Rise/fall time: 10%-90% transition time. Key for SI.
- Frequency: From period (1/T). Accuracy depends on scope timebase stability.
- Duty cycle: percentage of time high or low. For PWM verification.
- Jitter: Cycle-to-cycle or time-interval error (TIE). Measured with statistics,
  histogram, or eye diagram.

### 8.2 Logic Analyzer

Sampling rate: 10-20x the fastest signal for accurate timing. 100MHz signal ->
1-2GSa/s. For protocol decode only: 4-5x bus speed is sufficient.

Channels: 8 for I2C+SPI, 16+ for parallel buses or multi-channel SPI flash.

Protocol decoding: Common: UART, I2C, SPI, CAN, I2S (audio), 1-Wire (Dallas/
Maxim), PS/2, MDIO, SMI, SWD, JTAG.

Hardware: Saleae (Logic 8, Logic Pro 8/16: 100-500MSa/s), DSlogic (cheaper
alternative), Open Bench Logic Sniffer (open-source), Bus Pirate (slow but
versatile -- debug, scriptable, multi-protocol).

### 8.3 Multimeter

Accuracy: Basic DC accuracy as % of reading + number of counts. A 0.05%+2
multimeter reading 5.000V: error = 5.000 x 0.0005 + 0.002 = +/-4.5mV.

Digits/counts: 3.5 digits = 1999 counts (max display). 4.5 = 19999. 5.5 = 199999.
6.5+ digits = precision bench DMM. Typical handheld: 6000 counts (e.g., Fluke
117 displays 0.000-6.000 in 6V range).

True RMS: Measures AC RMS accurately for non-sinusoidal waveforms (rectified
sine, triangle, PWM). AC-only multimeters assume sine wave and give wrong
readings on other shapes.

4-wire (Kelvin) resistance: Separate source (force) and sense leads. Force leads
drive current through DUT; sense leads measure voltage drop at DUT terminals
(high-Z, no current). Eliminates lead resistance from measurement. Essential for
<1 ohm measurements.

Capacitance measurement: Small value (pF-nF): measure at fixed frequency. Large
value (uF-mF): measure time constant (charge/discharge time). Accuracy: +/-1-5%.

Diode test: Drives ~1mA through diode, displays forward voltage. Silicon: 0.5-
0.8V. Schottky: 0.15-0.45V. LED: 1.2-3.4V (may not light up, current limited).
Reverse: OL (over limit). Short: ~0V.

Input impedance: 10M ohm typical for DCV (some meters: >10G ohm up to 10V, then
10M ohm). Current inputs: shunt resistor (10A jack: 0.01 ohm; mA/uA jack: higher
shunt = higher burden voltage -- check datasheet).

### 8.4 Power Supply

CV (constant voltage) vs CC (constant current) modes: Set both V and I limits.
When load draws < I_limit, supply is in CV mode (regulates V). When load tries
to draw > I_limit, supply enters CC mode (regulates I, V drops). CC mode is
useful for: LED driving (no resistor needed), battery charging (set to charge
voltage + current limit), initial power-up (limit inrush current to find shorts).

Remote sensing (4-wire): Additional sense leads connect to load terminals,
compensating for voltage drop in the power leads. Without sensing: V_load =
V_supply - (I_load x 2 x R_lead). With 1A through 1m ohm leads: 2mV drop --
negligible. With 10A through 100m ohm leads: 2V drop -- catastrophic without
remote sense.

Series operation: Connect two channels in series for higher voltage. Floating
outputs only (check that negative terminal is isolated from earth ground).

Parallel operation: Most supplies cannot be directly paralleled (the one with
slightly higher V takes all the current). Use supplies designed for parallel
operation (master-slave with current sharing), or use external balancing (diodes
for OR-ing with Vf losses, or ideal diode controllers like LTC4357).

### 8.5 Function Generator

Arbitrary waveform generator (AWG): Stores a user-defined waveform in memory.
Output via DAC. Sample rate and memory depth determine the ability to reproduce
complex shapes. 14-bit DAC, 125MSa/s is typical for mid-range.

Modulation: AM (amplitude modulation): V_out = A_c x (1 + m x cos(2*pi*f_m*t)) x
cos(2*pi*f_c*t). FM (frequency modulation): f(t) = f_c + delta_f x cos(2*pi*f_m*t).
FSK (frequency shift keying): digital FM, switches between two frequencies.
Sweep: linear or logarithmic frequency sweep over a range -- use for Bode plot
manually (oscilloscope + function gen sweep).

### 8.6 Spectrum Analyzer

Frequency range: maximum frequency determines what signals can be seen. 3GHz is
entry-level; 26.5GHz for most wireless/radar.

RBW (Resolution Bandwidth): The IF filter bandwidth. Smaller RBW = better
frequency resolution, lower noise floor, slower sweep. For two tones 10kHz
apart, RBW <= 3kHz (1/3 of separation) to resolve them.

VBW (Video Bandwidth): Smooths the displayed trace. VBW < 0.1 x RBW reduces
noise (peak detection mode) or averages power.

Noise floor: DANL (Displayed Average Noise Level) typically -140 to -165 dBm/Hz.
Lower DANL = better sensitivity for weak signals. Preamplifier (built-in) lowers
noise floor by 15-30 dB but reduces dynamic range.

Tracking generator: Outputs a swept sine at the analyzer's tuned frequency.
Paired with the analyzer's input, measures the frequency response (S21 magnitude)
of filters, amplifiers, cables. Without tracking generator: need external signal
source.

### 8.7 Vector Network Analyzer (VNA)

S-parameters (scattering parameters): Describe how a network responds to signals
at its ports in terms of reflection and transmission.

- S11: Input reflection coefficient (port 1). Return loss = -20 x log10(|S11|).
  S11 = (Z_in - Z_0)/(Z_in + Z_0). Good match: S11 < -10 dB (VSWR < 2:1).
  < -20 dB is excellent.
- S21: Forward transmission (from port 1 to port 2). Insertion loss = -20 x
  log10(|S21|). In a filter: passband S21 ~ 0 dB; stopband S21 << -20 dB.
- S22: Output reflection coefficient (port 2).
- S12: Reverse transmission (port 2 to port 1). For passive reciprocal devices
  (filters, cables, antennas): S12 = S21.

Smith chart: Maps impedance on a polar plot. Center = Z_0 (perfect match, 50
ohms). Left half: impedances < Z_0 (capacitive). Right half: impedances > Z_0
(inductive). Outer circle: pure reactance (|Gamma| = 1, total reflection). Use
for: antenna impedance matching (matching network design), amplifier stability
circles, oscillator design.

Antenna matching: Measure S11 of antenna. If not at 50 ohms at target frequency,
design an LC matching network (L-network: series L + shunt C, or series C +
shunt L) to transform antenna Z to 50 ohms. Use Smith chart software (SimSmith,
ADS, or the VNA's built-in matching tool).

### 8.8 LCR Meter

Test frequency: Dielectric behavior of capacitors and permeability of inductors
vary with frequency. A capacitor measured at 100Hz may show different C than at
100kHz (dielectric relaxation, ESR). Always measure at or near the intended
operating frequency.

Series vs parallel equivalent: |Z| and phase are measured; the equivalent R and
C/L depend on whether you model them in series or parallel:
- Series: R_s + jX_s (for low-impedance devices, X < 100 ohms). Use for
  capacitors with low ESR, inductors with low DCR.
- Parallel: 1/(1/R_p + 1/jX_p) (for high-impedance devices, X > 100 ohms). Use
  for small capacitors, high-value resistors.

D (dissipation factor) = 1/Q = ESR / X_C = tan(delta). Lower D = better capacitor.
C0G: D < 0.001 at 1MHz. X7R: D ~ 0.01-0.05. Electrolytic: D ~ 0.05-0.5.

Q (quality factor) = X_L / R_s. Higher Q = better inductor. Air-core inductor:
Q ~ 100-300. Ferrite inductor: Q ~ 20-80 (loss in core reduces Q).

---

## 9. Industry Standards and Certifications

### 9.1 Safety Standards

UL 60950-1 (superseded by UL 62368-1): Safety of IT equipment. Covers: electric
shock, fire, mechanical hazards, thermal hazards, radiation. Replaced by IEC
62368-1 (hazard-based safety engineering: HBSE approach -- identify energy
sources, classify hazards, apply safeguards).

IEC 61010: Safety for measurement, control, and laboratory equipment. Applies to
multimeters, oscilloscopes, power supplies, lab equipment. Categories:
- CAT I: protected electronic circuits (low energy).
- CAT II: single-phase appliances, outlets.
- CAT III: three-phase distribution, including industrial wiring.
- CAT IV: three-phase at utility connection (outdoor, service entrance).
Higher CAT = higher transient withstand voltage. A CAT III 600V multimeter must
withstand 6kV transients.

IEC 60335: Household appliances. Covers: electric shock, fire, mechanical,
thermal, leakage current.

### 9.2 EMC Standards

FCC Part 15 (USA): Radiated emissions (30MHz-1GHz+), conducted emissions
(150kHz-30MHz), intentional radiator limits (WiFi, BT, etc.). Test at FCC-
listed lab or self-declare (DoC) with accredited lab.

CISPR 32 / EN 55032 (International/Europe): Multimedia equipment (replaces
CISPR 13 broadcast + CISPR 22 ITE). Harmonized with FCC Part 15.

CISPR 25 (Automotive): Emissions from components/modules used in vehicles.
Significantly stricter than consumer standards due to onboard receivers (AM/FM,
GPS, cellular, V2X).

### 9.3 Environmental Standards

RoHS (Restriction of Hazardous Substances): EU Directive 2011/65/EU + amendment
(EU) 2015/863. Restricts 10 substances:
- Lead (Pb) < 0.1%
- Mercury (Hg) < 0.1%
- Cadmium (Cd) < 0.01%
- Hexavalent chromium (Cr6+) < 0.1%
- Polybrominated biphenyls (PBB) < 0.1%
- Polybrominated diphenyl ethers (PBDE) < 0.1%
- Plus 4 phthalates: DEHP, BBP, DBP, DIBP < 0.1% each
Exemptions: lead in high-melting-temperature solder (>85% Pb), lead in ceramic
capacitors, etc. Must declare compliance in BOM.

REACH (Registration, Evaluation, Authorisation of Chemicals): EU Regulation
EC 1907/2006. Requires registration of chemical substances. SVHC (Substances of
Very High Concern) list updated every 6 months. Manufacturers must declare if
articles contain >0.1% w/w of any SVHC.

WEEE (Waste Electrical and Electronic Equipment): EU Directive 2012/19/EU.
Mandates take-back, recycling, and proper disposal. Products must carry the
crossed-out wheelie-bin symbol.

### 9.4 Reliability Standards

IPC-A-610: Acceptability of Electronic Assemblies. Defines what constitutes an
acceptable solder joint, component placement, and assembly workmanship. Classes:
- Class 1: General electronic products (toys, consumer).
- Class 2: Dedicated service (industrial, communications -- longer life
  expected).
- Class 3: High performance/harsh environment (aerospace, medical, military --
  failure is life-threatening).

J-STD-001: Requirements for Soldered Electrical and Electronic Assemblies. The
process standard (how to solder) that complements IPC-A-610 (what the result
should look like).

IPC-2221: Generic Standard on Printed Board Design. Covers: board types,
materials, current carrying capacity, clearance/creepage, thermal management,
test coupon design.

ISO 9001: Quality Management Systems. Generic standard -- not electronics-
specific, but widely required for contract manufacturing and supplier
qualification. Certifies that the organization has documented processes and
follows them.

### 9.5 Automotive Standards

AEC-Q100: Stress test qualification for ICs in automotive. Grades:
- Grade 0: -40 to +150C (engine compartment, transmission).
- Grade 1: -40 to +125C (underhood).
- Grade 2: -40 to +105C (passenger compartment).
- Grade 3: -40 to +85C (passenger compartment, less harsh).
Tests include: HTOL (high-temperature operating life), HAST (highly accelerated
stress test), TC (temperature cycling), ESD, latch-up.

AEC-Q200: Same as AEC-Q100 but for passive components (resistors, capacitors,
inductors, crystals).

ISO 26262: Road Vehicles -- Functional Safety. ASIL (Automotive Safety Integrity
Level) A-D: ASIL A = lowest risk (minor injury), ASIL D = highest risk
(possible fatality without safety mechanisms). Requires: hazard analysis and
risk assessment (HARA), safety goals, functional safety concept, technical
safety concept, hardware/software safety requirements, FMEDA, fault injection
testing.

### 9.6 Medical Standards

IEC 60601: Medical electrical equipment. Key requirements:
- Isolation: Patient-connected parts must have 2 MOPP (Means of Patient
  Protection) -- two independent layers of isolation. Creepage/clearance for
  2 MOPP is doubled vs standard 1 MOPP.
- Leakage current: Patient leakage < 10uA NC (normal condition), < 50uA SFC
  (single fault condition). Earth leakage < 500uA NC, < 1000uA SFC.
- Essential performance: Functions that must continue working (defibrillation
  protection in ECG monitors, alarm functions in infusion pumps).
- Risk management per ISO 14971: Identify hazards, assess risk, implement
  controls, verify effectiveness.
- EMC: IEC 60601-1-2 -- stricter than commercial EMC (immunity levels 10V/m
  vs 3V/m for commercial, conducted immunity 6V vs 3V).

### 9.7 IP Ratings

IP (Ingress Protection) per IEC 60529:

First digit (solid particle protection):
- 0: No protection
- 1: >50mm objects (hand)
- 2: >12.5mm (finger)
- 3: >2.5mm (tool, wire)
- 4: >1mm (small wire)
- 5: Dust protected (limited ingress)
- 6: Dust-tight (no ingress)

Second digit (liquid ingress protection):
- 0: No protection
- 1: Dripping water (vertical)
- 2: Dripping water (15 degree tilt)
- 3: Spraying water (60 degree from vertical)
- 4: Splashing water
- 5: Water jet (6.3mm nozzle, 12.5 L/min, 30 kPa at 3m)
- 6: Powerful water jet (12.5mm nozzle, 100 L/min, 100 kPa at 3m)
- 6K: Same as 6 but with higher pressure (applies to road vehicles)
- 7: Immersion up to 1m for 30 minutes
- 8: Immersion beyond 1m (conditions specified by manufacturer)
- 9K: High-temperature, high-pressure spray (steam cleaning)

Common ratings:
- IP20: Indoor electronics, no water protection (typical open-frame electronics).
- IP54: Dust protected, splash resistant (outdoor enclosures with weather seal).
- IP67: Dust-tight, immersion to 1m (outdoor sensors, marine electronics, phones).
- IP68: Dust-tight, continuous immersion (specified depth/time, underwater
  equipment).

Note: IP rating is for electrical enclosures and connectors. A connector rated
IP67 means it is dust-tight and immersible when mated -- unmated, it may have
no protection.