"""Simple FizzBuzz with a deliberate off-by-one bug."""


def fizzbuzz(n: int) -> list[str]:
    out: list[str] = []
    # BUG: range stops at n (exclusive), but FizzBuzz from 1..=n needs n+1.
    for i in range(1, n):
        if i % 15 == 0:
            out.append("FizzBuzz")
        elif i % 3 == 0:
            out.append("Fizz")
        elif i % 5 == 0:
            out.append("Buzz")
        else:
            out.append(str(i))
    return out


if __name__ == "__main__":
    print(fizzbuzz(15))
