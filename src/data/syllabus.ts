import type { Chapter, SubjectKey, Tier, Klass, Topic } from '../types';

// ---------------------------------------------------------------------------
// High-Weightage Register + full BITSAT syllabus, pre-populated.
// Tier 1 = high weightage (protect at all costs), Tier 2 = low weightage
// (auto-droppable when the timeline compresses).
// ---------------------------------------------------------------------------

interface RawTopic {
  name: string;
  h?: number; // est hours (default 1.5)
  q?: number; // target questions (default 25)
}

let topicSeq = 0;
function mkTopics(chapterId: string, raw: RawTopic[]): Topic[] {
  return raw.map((t) => ({
    id: `${chapterId}-t${topicSeq++}`,
    name: t.name,
    estHours: t.h ?? 1.5,
    targetQuestions: t.q ?? 25,
  }));
}

function chapter(
  id: string,
  subject: SubjectKey,
  name: string,
  klass: Klass,
  tier: Tier,
  weight: number,
  topics: RawTopic[]
): Chapter {
  return { id, subject, name, klass, tier, weight, topics: mkTopics(id, topics) };
}

// ----------------------------- MATHEMATICS ---------------------------------
const MATH: Chapter[] = [
  // Tier 1 — Coordinate Geometry
  chapter('m-straight-lines', 'math', 'Straight Lines', 11, 1, 95, [
    { name: 'Slope, forms of a line & angle between lines' },
    { name: 'Distance, foot of perpendicular & family of lines' },
    { name: 'Pair of straight lines & angle bisectors' },
  ]),
  chapter('m-circles', 'math', 'Circles', 11, 1, 92, [
    { name: 'Equation forms, tangents & normals' },
    { name: 'Power of a point, chord of contact & radical axis' },
    { name: 'Family of circles & orthogonality' },
  ]),
  chapter('m-conics', 'math', 'Conic Sections (Parabola/Ellipse/Hyperbola)', 11, 1, 96, [
    { name: 'Parabola — focal chord, tangent & normal' },
    { name: 'Ellipse — eccentricity, director circle, tangents' },
    { name: 'Hyperbola — asymptotes, conjugate & rectangular' },
  ]),
  // Tier 1 — Vectors & 3D
  chapter('m-vectors', 'math', 'Vectors', 12, 1, 90, [
    { name: 'Dot & cross product, projections' },
    { name: 'Scalar & vector triple products' },
  ]),
  chapter('m-3d', 'math', '3D Geometry', 12, 1, 93, [
    { name: 'Direction ratios, line & plane equations' },
    { name: 'Distance between lines, planes & angles' },
    { name: 'Shortest distance & image of a point' },
  ]),
  // Tier 1 — Calculus
  chapter('m-limits', 'math', 'Limits & Continuity', 11, 1, 90, [
    { name: 'Standard limits, L’Hôpital & expansions' },
    { name: 'Continuity & differentiability checks' },
  ]),
  chapter('m-aod', 'math', 'Differentiation & Applications (AoD)', 12, 1, 94, [
    { name: 'Differentiation techniques & implicit functions' },
    { name: 'Tangents, normals, monotonicity' },
    { name: 'Maxima–minima & rate of change' },
  ]),
  chapter('m-integrals', 'math', 'Integral Calculus', 12, 1, 95, [
    { name: 'Indefinite integration techniques' },
    { name: 'Definite integrals & properties' },
    { name: 'Area under curves & applications' },
  ]),
  // Tier 1 — Matrices & Determinants
  chapter('m-matrices', 'math', 'Matrices & Determinants', 12, 1, 88, [
    { name: 'Matrix algebra, inverse & rank' },
    { name: 'Determinant properties & Cramer’s rule' },
    { name: 'System of equations — consistency' },
  ]),
  // Tier 2
  chapter('m-trig', 'math', 'Trigonometry', 11, 2, 70, [
    { name: 'Identities, equations & general solutions' },
    { name: 'Inverse trig & properties' },
  ]),
  chapter('m-complex', 'math', 'Complex Numbers', 11, 2, 65, [
    { name: 'Modulus, argument & De Moivre' },
    { name: 'Roots of unity & loci' },
  ]),
  chapter('m-quad', 'math', 'Quadratic Equations & Theory of Equations', 11, 2, 62, [
    { name: 'Nature of roots, relations & transformations' },
  ]),
  chapter('m-pnc', 'math', 'Permutations & Combinations', 11, 2, 58, [
    { name: 'Arrangements, selections & distributions' },
  ]),
  chapter('m-binomial', 'math', 'Binomial Theorem', 11, 2, 60, [
    { name: 'General term, middle term & properties' },
  ]),
  chapter('m-prob', 'math', 'Probability', 12, 2, 68, [
    { name: 'Conditional probability & Bayes theorem' },
    { name: 'Binomial distribution & random variables' },
  ]),
  chapter('m-sequence', 'math', 'Sequences & Series', 11, 2, 55, [
    { name: 'AP, GP, HP & special series' },
  ]),
  chapter('m-functions', 'math', 'Sets, Relations & Functions', 11, 2, 52, [
    { name: 'Domain, range, composition & inverse' },
  ]),
  chapter('m-stats', 'math', 'Statistics', 11, 2, 40, [
    { name: 'Mean, variance & standard deviation' },
  ]),
  chapter('m-lpp', 'math', 'Linear Programming', 12, 2, 35, [
    { name: 'Graphical method & feasible regions' },
  ]),
];

// ------------------------------- PHYSICS -----------------------------------
const PHYSICS: Chapter[] = [
  // Tier 1
  chapter('p-electrostatics', 'physics', 'Electrostatics', 12, 1, 94, [
    { name: 'Coulomb’s law, field & Gauss’s law' },
    { name: 'Potential, dipoles & capacitors' },
  ]),
  chapter('p-current', 'physics', 'Current Electricity', 12, 1, 92, [
    { name: 'Ohm’s law, resistivity & networks' },
    { name: 'Kirchhoff’s laws, Wheatstone & potentiometer' },
  ]),
  chapter('p-thermo', 'physics', 'Heat & Thermodynamics', 11, 1, 90, [
    { name: 'Calorimetry, expansion & kinetic theory' },
    { name: 'Laws of thermodynamics & processes' },
  ]),
  chapter('p-modern', 'physics', 'Modern Physics & Semiconductors', 12, 1, 95, [
    { name: 'Photoelectric effect & dual nature' },
    { name: 'Atoms, nuclei & radioactivity' },
    { name: 'Semiconductors, diodes & logic gates' },
  ]),
  chapter('p-workenergy', 'physics', 'Work, Energy & Power', 11, 1, 85, [
    { name: 'Work–energy theorem & conservation' },
  ]),
  chapter('p-rotation', 'physics', 'Rotational Motion & Moment of Inertia', 11, 1, 88, [
    { name: 'Moment of inertia & torque' },
    { name: 'Angular momentum & rolling motion' },
  ]),
  // Tier 2
  chapter('p-kinematics', 'physics', 'Kinematics', 11, 2, 60, [
    { name: 'Motion in 1D & 2D, projectiles' },
  ]),
  chapter('p-laws', 'physics', 'Laws of Motion', 11, 2, 62, [
    { name: 'Newton’s laws, friction & constraints' },
  ]),
  chapter('p-gravitation', 'physics', 'Gravitation', 11, 2, 55, [
    { name: 'Field, potential & orbital mechanics' },
  ]),
  chapter('p-shm', 'physics', 'Oscillations (SHM)', 11, 2, 64, [
    { name: 'SHM equations, springs & pendulums' },
  ]),
  chapter('p-waves', 'physics', 'Waves & Sound', 11, 2, 58, [
    { name: 'Wave equation, superposition & Doppler' },
  ]),
  chapter('p-emi', 'physics', 'Magnetism, EMI & AC', 12, 2, 72, [
    { name: 'Magnetic field, force & moving charges' },
    { name: 'EMI, inductance & AC circuits' },
  ]),
  chapter('p-optics', 'physics', 'Ray & Wave Optics', 12, 2, 70, [
    { name: 'Reflection, refraction & lenses' },
    { name: 'Interference, diffraction & polarisation' },
  ]),
  chapter('p-fluids', 'physics', 'Mechanical Properties (Fluids & Elasticity)', 11, 2, 45, [
    { name: 'Elasticity, surface tension & Bernoulli' },
  ]),
  chapter('p-units', 'physics', 'Units, Dimensions & Errors', 11, 2, 38, [
    { name: 'Dimensional analysis & error propagation' },
  ]),
];

// ------------------------------ CHEMISTRY ----------------------------------
const CHEMISTRY: Chapter[] = [
  // Tier 1
  chapter('c-bonding', 'chemistry', 'Chemical Bonding', 11, 1, 95, [
    { name: 'VSEPR theory & molecular geometry' },
    { name: 'Hybridisation, MOT & bond parameters' },
  ]),
  chapter('c-goc', 'chemistry', 'General Organic Chemistry (GOC)', 11, 1, 94, [
    { name: 'Inductive, resonance & hyperconjugation' },
    { name: 'Stability of carbocations, carbanions & radicals' },
  ]),
  chapter('c-coordination', 'chemistry', 'Coordination Compounds', 12, 1, 90, [
    { name: 'Nomenclature, isomerism & VBT' },
    { name: 'Crystal field theory & magnetic behaviour' },
  ]),
  chapter('c-pblock', 'chemistry', 'p-Block Elements (NCERT)', 12, 1, 88, [
    { name: 'Group 13–14 trends & compounds' },
    { name: 'Group 15–18 trends & compounds' },
  ]),
  chapter('c-dfblock', 'chemistry', 'd- & f-Block Elements (NCERT)', 12, 1, 86, [
    { name: 'Transition metal trends & KMnO4/K2Cr2O7' },
    { name: 'Lanthanoid & actinoid contraction' },
  ]),
  chapter('c-kinetics', 'chemistry', 'Chemical Kinetics (Numericals)', 12, 1, 84, [
    { name: 'Rate laws, order & integrated equations' },
    { name: 'Arrhenius equation & half-life' },
  ]),
  chapter('c-electrochem', 'chemistry', 'Electrochemistry (Numericals)', 12, 1, 85, [
    { name: 'Nernst equation & EMF' },
    { name: 'Conductance & electrolysis (Faraday)' },
  ]),
  chapter('c-equilibrium', 'chemistry', 'Equilibrium — Ionic & Chemical (Numericals)', 11, 1, 87, [
    { name: 'Kc, Kp & Le Chatelier' },
    { name: 'pH, buffers & solubility product' },
  ]),
  // Tier 2
  chapter('c-atomic', 'chemistry', 'Atomic Structure', 11, 2, 66, [
    { name: 'Quantum numbers & electronic configuration' },
  ]),
  chapter('c-mole', 'chemistry', 'Mole Concept & Stoichiometry', 11, 2, 64, [
    { name: 'Mole calculations & limiting reagent' },
  ]),
  chapter('c-thermo', 'chemistry', 'Chemical Thermodynamics', 11, 2, 68, [
    { name: 'Enthalpy, entropy & Gibbs free energy' },
  ]),
  chapter('c-solutions', 'chemistry', 'Solutions & Colligative Properties', 12, 2, 60, [
    { name: 'Raoult’s law & colligative properties' },
  ]),
  chapter('c-hydrocarbons', 'chemistry', 'Hydrocarbons', 11, 2, 58, [
    { name: 'Alkanes, alkenes, alkynes & aromatics' },
  ]),
  chapter('c-haloalkanes', 'chemistry', 'Haloalkanes & Haloarenes', 12, 2, 55, [
    { name: 'SN1/SN2 & elimination reactions' },
  ]),
  chapter('c-oxygen-org', 'chemistry', 'Alcohols, Aldehydes, Acids & Amines', 12, 2, 70, [
    { name: 'Alcohols, phenols & ethers' },
    { name: 'Carbonyls, carboxylic acids & amines' },
  ]),
  chapter('c-biomolecules', 'chemistry', 'Biomolecules & Polymers', 12, 2, 42, [
    { name: 'Carbohydrates, proteins & polymers' },
  ]),
  chapter('c-sblock', 'chemistry', 's-Block & Periodic Properties', 11, 2, 50, [
    { name: 'Periodic trends & s-block compounds' },
  ]),
];

// --------------------------- ENGLISH & LR ----------------------------------
// Continuous module — fed through the 45-min slot, 4x/week.
const ENGLISH: Chapter[] = [
  chapter('e-grammar', 'english', 'English Proficiency — Grammar', 12, 1, 50, [
    { name: 'Error spotting, sentence improvement & tenses', h: 0.75, q: 20 },
    { name: 'Prepositions, articles & subject–verb agreement', h: 0.75, q: 20 },
  ]),
  chapter('e-vocab', 'english', 'English Proficiency — Vocabulary', 12, 1, 45, [
    { name: 'Synonyms, antonyms & one-word substitution', h: 0.75, q: 25 },
    { name: 'Idioms, phrases & contextual usage', h: 0.75, q: 25 },
  ]),
  chapter('e-rc', 'english', 'English Proficiency — Reading Comprehension', 12, 1, 48, [
    { name: 'Passage comprehension & inference', h: 0.75, q: 12 },
  ]),
  chapter('e-lr-verbal', 'english', 'Logical Reasoning — Verbal', 12, 1, 52, [
    { name: 'Analogies, series & coding–decoding', h: 0.75, q: 22 },
    { name: 'Blood relations, directions & syllogisms', h: 0.75, q: 22 },
  ]),
  chapter('e-lr-nonverbal', 'english', 'Logical Reasoning — Non-Verbal', 12, 1, 50, [
    { name: 'Figure series, analogies & pattern completion', h: 0.75, q: 22 },
  ]),
];

export const CHAPTERS: Chapter[] = [...MATH, ...PHYSICS, ...CHEMISTRY, ...ENGLISH];

export const SUBJECT_META: Record<
  SubjectKey,
  { label: string; short: string; color: string; questions: number }
> = {
  math: { label: 'Mathematics', short: 'Math', color: '#38bdf8', questions: 40 },
  physics: { label: 'Physics', short: 'Phys', color: '#a78bfa', questions: 30 },
  chemistry: { label: 'Chemistry', short: 'Chem', color: '#34d399', questions: 30 },
  english: { label: 'English & Logical Reasoning', short: 'Eng & LR', color: '#fbbf24', questions: 30 },
};

// Fast lookups -------------------------------------------------------------
export const CHAPTER_BY_ID: Record<string, Chapter> = Object.fromEntries(
  CHAPTERS.map((c) => [c.id, c])
);

export const TOPIC_INDEX: Record<
  string,
  { topic: Topic; chapter: Chapter }
> = Object.fromEntries(
  CHAPTERS.flatMap((c) => c.topics.map((t) => [t.id, { topic: t, chapter: c }]))
);

export const ALL_TOPIC_IDS: string[] = CHAPTERS.flatMap((c) => c.topics.map((t) => t.id));
