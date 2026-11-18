import { minimatch, Minimatch } from 'minimatch';

const normalize = (options = {}) => ({
  nocase: options.nocase,
  dot: options.dot,
  nonegate: options.noext ? true : options.nonegate,
  noglobstar: options.noglobstar,
  noext: options.noext,
  nocomment: options.nocomment,
  matchBase: options.matchBase ?? options.basename,
  windowsPathsNoEscape: options.windows,
  platform: options.platform
});

export const isMatch = (str, patterns, options = {}) => {
  const list = Array.isArray(patterns) ? patterns : [patterns];
  return list.some(pattern => minimatch(str, pattern, normalize(options)));
};

export const makeRe = (pattern, options = {}) => {
  const mm = new Minimatch(pattern, normalize(options));
  return mm.makeRe();
};

export const scan = (input, options = {}) => {
  const mm = new Minimatch(input, normalize(options));
  return {
    prefix: mm.set?.[0]?.[0]?.toString?.() ?? '',
    input,
    start: 0,
    base: mm.globParts?.[0]?.join?.('/') ?? '',
    glob: input,
    isBrace: /[{}]/.test(input),
    isBracket: /\[[^\]]*\]/.test(input),
    isGlob: /[*?+@!()[\]{}]/.test(input),
    isExtglob: /[+@!?*]\(/.test(input),
    negated: input.startsWith('!'),
    negatedExtglob: false
  };
};

export const parse = (pattern, options = {}) => ({
  input: pattern,
  output: pattern,
  tokens: [],
  options: normalize(options)
});

export const picomatch = (patterns, options = {}) => {
  const list = Array.isArray(patterns) ? patterns : [patterns];
  const matcher = str => list.some(pattern => minimatch(str, pattern, normalize(options)));
  matcher.test = matcher;
  matcher.match = str => matcher(str);
  matcher.isMatch = matcher;
  matcher.scan = input => scan(input, options);
  matcher.parse = pattern => parse(pattern, options);
  matcher.makeRe = pattern => makeRe(pattern, options);
  matcher.state = parse(list[0], options);
  return matcher;
};

picomatch.test = (str, pattern, options) => isMatch(str, pattern, options);
picomatch.isMatch = isMatch;
picomatch.makeRe = makeRe;
picomatch.scan = scan;
picomatch.parse = parse;
picomatch.matchBase = (str, pattern, options) => minimatch(str, pattern, {...normalize(options), matchBase: true});

export default picomatch;
