# React hooks

## useState

useState returns the current value and a setter, for example const [count, setCount] = useState(0). State updates are batched and asynchronous, so use the functional form setCount(c => c + 1) when the next value depends on the previous one.

## useEffect

useEffect runs side effects after render, such as fetching data or subscribing to events. The dependency array controls when it re-runs, an empty array runs it once, and the returned cleanup function runs before the next effect and on unmount.

## useMemo and useCallback

useMemo caches an expensive calculation and useCallback caches a function identity between renders. They only help when the value is passed to a memoized child or used as a dependency, so measure before adding them.

## Context

createContext and useContext pass data deeply without prop drilling. Every consumer re-renders when the provider value changes, so split contexts or memoize the value for frequently changing data.

## Keys and lists

Give list items a stable key such as a database id so React can match elements between renders. Using the array index as key breaks state when items are reordered.
