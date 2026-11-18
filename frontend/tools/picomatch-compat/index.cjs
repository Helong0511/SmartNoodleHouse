const mm = require('minimatch');

const normalize = (options = {}) => ({
  nocase: options.nocase,
  dot: options.dot,
  nonegate: options.noext ? true : options.nonegate,
  noglobstar: options.noglobstar,
  noext: options.noext,
  nocomment: options.nocomment,
  matchBase: options.matchBase ?? options.basename,
  windowsPathsNoEscape: options.windows
});

const isMatch = (str, patterns, options = {}) => {
  const list = Array.isArray(patterns) ? patterns : [patterns];
  return list.some(pattern => mm.minimatch(str, pattern, normalize(options)));
};
const makeRe = (pattern, options = {}) => new mm.Minimatch(pattern, normalize(options)).makeRe();
const scan = (input, options = {}) => ({ prefix: '', input, start: 0, base: '', glob: input, isGlob: /[*?+@!()[\]{}]/.test(input), isExtglob: /[+@!?*]\(/.test(input), isBrace: /[{}]/.test(input), isBracket: /\[[^\]]*\]/.test(input), negated: input.startsWith('!'), negatedExtglob: false });
const parse = (pattern, options = {}) => ({ input: pattern, output: pattern, tokens: [], options: normalize(options) });
const picomatch = (patterns, options = {}) => { const list = Array.isArray(patterns) ? patterns : [patterns]; const matcher = str => list.some(pattern => mm.minimatch(str, pattern, normalize(options))); matcher.test=matcher; matcher.match=matcher; matcher.isMatch=matcher; matcher.scan=x=>scan(x,options); matcher.parse=x=>parse(x,options); matcher.makeRe=x=>makeRe(x,options); matcher.state=parse(list[0],options); return matcher; };
picomatch.test=(str,pattern,options)=>isMatch(str,pattern,options);
picomatch.isMatch=isMatch; picomatch.makeRe=makeRe; picomatch.scan=scan; picomatch.parse=parse;
module.exports=picomatch;
module.exports.default=picomatch;
