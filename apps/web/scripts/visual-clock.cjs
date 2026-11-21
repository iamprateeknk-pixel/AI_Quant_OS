const RealDate = Date;
const fixedTimestamp = RealDate.parse('2026-10-20T12:00:00.000Z');

class FixedDate extends RealDate {
  constructor(...args) {
    super(...(args.length ? args : [fixedTimestamp]));
  }

  static now() {
    return fixedTimestamp;
  }

  static parse(value) {
    return RealDate.parse(value);
  }

  static UTC(...args) {
    return RealDate.UTC(...args);
  }
}

global.Date = FixedDate;
