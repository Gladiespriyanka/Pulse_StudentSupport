// In-memory session state only. Nothing sensitive is persisted in browser storage; sessions are httpOnly cookies.
export const S = { me: null, meta: null, can: (p) => !!S.me && S.me.permissions.includes(p), any: (...p) => p.some((x) => S.can(x)) };
export const isStudent = () => S.me && S.me.role === 'student';
