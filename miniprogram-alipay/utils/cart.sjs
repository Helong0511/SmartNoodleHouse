function hasId(list, id) {
  if (!list || !list.length) return false
  for (var i = 0; i < list.length; i++) {
    if (list[i] == id) return true
  }
  return false
}

export default {
  hasId: hasId
}
