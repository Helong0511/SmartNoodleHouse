function buildRowList(rows) {
  const n = Math.max(1, parseInt(rows, 10) || 4)
  const list = []
  for (let i = 0; i < n; i++) list.push(i)
  return list
}

Component({
  properties: {
    type: {
      type: String,
      value: 'menu'
    },
    rows: {
      type: Number,
      value: 4
    }
  },
  data: {
    rowList: [0, 1, 2, 3]
  },
  observers: {
    rows(n) {
      this.setData({ rowList: buildRowList(n) })
    }
  },
  attached() {
    this.setData({ rowList: buildRowList(this.properties.rows) })
  }
})
