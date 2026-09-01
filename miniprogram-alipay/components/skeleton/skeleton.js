function buildRowList(rows) {
  const n = Math.max(1, parseInt(rows, 10) || 4)
  const list = []
  for (let i = 0; i < n; i++) list.push(i)
  return list
}

Component({
  props: {
    type: {
      type: String,
      default: 'menu'
    },
    rows: {
      type: Number,
      default: 4
    }
  },
  data: {
    rowList: [0, 1, 2, 3]
  },
  didMount() {
    this.setData({ rowList: buildRowList(this.props.rows) })
  },
  deriveDataFromProps(nextProps) {
    this.setData({ rowList: buildRowList(nextProps.rows) })
  }
})
