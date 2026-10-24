# TypeScript types

## Interfaces and type aliases

Both interface and type describe object shapes. Interfaces can be extended and merged, while type aliases also name unions, intersections and tuples. Prefer interface for public object contracts.

## Unions and narrowing

A union such as string | number accepts either type. Narrow it with typeof, in checks or a discriminant property so the compiler knows which member is active inside each branch.

## Generics

Generics parameterise types, for example function first<T>(items: T[]): T. Constraints with extends limit what T may be, and defaults like <T = string> make type arguments optional.

## Utility types

Partial<T> makes every property optional, Required<T> makes them mandatory, Pick<T, K> and Omit<T, K> select or remove keys, and Record<K, V> builds an object type from keys.

## Strict mode

Set strict to true in tsconfig.json to enable strictNullChecks and noImplicitAny. Prefer unknown over any for untrusted values and narrow before use.
