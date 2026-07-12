import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import {
  ArrowRightLeft,
  BookOpen,
  Compass,
  ExternalLink,
  FolderTree,
  GitBranch,
  LineChart,
  Scissors,
  Sparkles,
  Tags,
  Target,
  Upload,
  Wallet,
} from 'lucide-react'

// Investments deliberately has no anchor here — it isn't one more topic in
// this walkthrough, it's a separate, unrelated part of the app (see the
// callout right below the intro).
const SECTIONS = [
  { id: 'overview', label: 'Overview' },
  { id: 'importing', label: 'Importing data' },
  { id: 'ledger', label: 'The ledger & postings' },
  { id: 'placeholders', label: 'Placeholder accounts' },
  { id: 'rules', label: 'Transfer rules' },
  { id: 'transfer-suggestions', label: 'Transfer suggestions' },
  { id: 'categorizing', label: 'Categorizing & tags' },
  { id: 'automated-categorization', label: 'Automated categorization' },
  { id: 'splitting', label: 'Splitting' },
  { id: 'budgets', label: 'Budgets' },
  { id: 'goals', label: 'Goals' },
]

// A visual callout for a term worth pinning down the moment it's first
// used, rather than only in a glossary at the very end — so the
// definition is right where the reading flow needs it.
function Definition({ term, children }: { term: string; children: ReactNode }) {
  return (
    <div className="my-4 rounded-lg border border-foreground/10 bg-muted/40 px-4 py-3">
      <p className="text-[10px] font-semibold tracking-wide text-muted-foreground uppercase">Definition</p>
      <p className="mt-1 text-sm leading-relaxed">
        <span className="font-semibold text-foreground">{term}</span>
        <span className="text-muted-foreground"> — </span>
        {children}
      </p>
    </div>
  )
}

function Section({ id, icon: Icon, title, children }: { id: string; icon: typeof BookOpen; title: string; children: ReactNode }) {
  return (
    <section id={id} className="scroll-mt-40 space-y-4 border-t border-border pt-10 first:border-t-0 first:pt-0">
      <h2 className="flex items-center gap-2.5 text-xl font-semibold tracking-tight text-foreground">
        <Icon className="size-5 text-muted-foreground" />
        {title}
      </h2>
      <div className="max-w-3xl space-y-4 text-sm leading-relaxed text-foreground/90">{children}</div>
    </section>
  )
}

function ExampleTable({ caption, columns, rows }: { caption: string; columns: string[]; rows: (string | number)[][] }) {
  return (
    <div className="my-4 overflow-x-auto rounded-lg border border-border">
      <table className="w-full border-collapse text-xs">
        <caption className="border-b border-border bg-muted/40 px-3 py-2 text-left font-medium text-muted-foreground">
          {caption}
        </caption>
        <thead>
          <tr className="border-b border-border bg-muted/20">
            {columns.map((column) => (
              <th key={column} className="px-3 py-2 text-left font-medium whitespace-nowrap text-muted-foreground">
                {column}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="font-mono">
          {rows.map((row, index) => (
            <tr key={index} className="border-b border-border/60 last:border-0">
              {row.map((cell, cellIndex) => (
                <td key={cellIndex} className="px-3 py-1.5 whitespace-nowrap">
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function AccountKindTable() {
  const kinds: [string, string][] = [
    ['checking', 'An everyday spending account.'],
    ['savings', 'An interest-bearing deposit account.'],
    ['credit_card', 'A revolving line of credit — its balance is usually negative (money owed).'],
    ['vault', 'A named sub-balance inside one of your own savings accounts, for banks that let you set money aside in labeled buckets without opening a separate account.'],
    ['cash', 'Physical cash you track by hand.'],
    ['loan', 'Money you owe outside of a credit card — a mortgage, a personal loan, and so on.'],
    ['income_source', 'Virtual — the generic "somewhere money came from" placeholder, used until a real payer is known. See Placeholder accounts below.'],
    ['expense_payee', 'Virtual — the generic "somewhere money went" placeholder, used until a real payee is known. See Placeholder accounts below.'],
    ['external_investment', "A placeholder that never tracks its own transactions — see Investments below."],
    ['other_asset', 'A manually-typed value (a car, a property) with no transaction history — just a number that counts toward net worth.'],
  ]
  return (
    <ExampleTable caption="Account kinds" columns={['Kind', 'What it means']} rows={kinds} />
  )
}

export function GuidePage() {
  return (
    <div className="flex-1 overflow-y-auto">
      <div className="sticky top-0 z-10 border-b border-border bg-white/95 backdrop-blur-sm">
        <div className="flex items-center justify-between px-8 py-5">
          <h1 className="text-lg font-semibold tracking-tight text-foreground">How this app works</h1>
        </div>
        <nav className="flex flex-wrap gap-x-5 gap-y-1 px-8 pb-3 text-sm text-muted-foreground">
          {SECTIONS.map((section) => (
            <a key={section.id} href={`#${section.id}`} className="transition-colors hover:text-foreground">
              {section.label}
            </a>
          ))}
        </nav>
      </div>

      <div className="mx-auto max-w-4xl space-y-10 px-8 py-8">
        <div className="rounded-lg border border-foreground/10 bg-gradient-to-br from-muted/60 to-transparent p-5">
          <p className="flex items-center gap-2 text-base font-semibold text-foreground">
            <Compass className="size-4 text-muted-foreground" />
            Read this once, and the rest of the app should feel obvious
          </p>
          <p className="mt-2 text-sm leading-relaxed text-muted-foreground">
            Every page here — Import, Accounting, Budget, Goals, Net Worth — is a different view onto one shared
            record of what actually happened in your real accounts. Nothing is duplicated or separately maintained:
            change how a transaction is categorized in one place, and every chart, budget, and goal that touches it
            updates the same way. This page walks through that shared foundation once, end to end, so the
            vocabulary and mechanics used everywhere else make sense the first time you see them.
          </p>
        </div>

        <Link
          to="/investments"
          className="group flex items-center justify-between gap-4 rounded-lg border border-dashed border-foreground/20 bg-transparent p-5 transition-colors hover:border-foreground/40 hover:bg-muted/30"
        >
          <div className="flex items-start gap-3">
            <LineChart className="mt-0.5 size-5 shrink-0 text-muted-foreground" />
            <div>
              <p className="font-semibold text-foreground">Looking for Investments?</p>
              <p className="mt-1 text-sm leading-relaxed text-muted-foreground">
                Everything below this point is about the shared cash-accounts side of the app — Import, Accounting,
                Budget, Goals. Investments isn't one more topic in that same story: it's a separate module tracking
                brokerage holdings, lots, and market performance, on its own page with its own explanation. The only
                connection between the two is one placeholder account kind,{' '}
                <code className="rounded bg-muted px-1 py-0.5 text-xs not-italic">external_investment</code>,
                mentioned in Importing data below.
              </p>
            </div>
          </div>
          <ExternalLink className="size-4 shrink-0 text-muted-foreground transition-transform group-hover:translate-x-0.5" />
        </Link>

        <Section id="overview" icon={Compass} title="Overview">
          <p>
            All of the pages in this app — <span className="font-medium text-foreground">Accounting</span>,{' '}
            <span className="font-medium text-foreground">Budget</span>,{' '}
            <span className="font-medium text-foreground">Goals</span>, and{' '}
            <span className="font-medium text-foreground">Net Worth</span> — work off exactly the same underlying
            data: the transactions you import on the <span className="font-medium text-foreground">Import</span>{' '}
            page. There's no separate "budget data" or "goals data" sitting off to the side — a budget is just a
            target number laid over the same transactions the Accounting page shows you, and a goal's balance is
            just a running total of money you've told the app to set aside from that same pool. Once data is in,
            every other page is a lens on it, not a separate copy of it.
          </p>
          <p>
            The one deliberate exception is{' '}
            <span className="font-medium text-foreground">Investments</span> (and the tracked-portfolio slice of Net
            Worth) — that side of the app is drastically different: it tracks brokerage holdings, lots, and market
            performance rather than bank transactions. It's a separate module with its own explanation, linked at the
            top of this page.
          </p>
        </Section>

        <Section id="importing" icon={Upload} title="Importing data">
          <p>
            Data enters the app in exactly one place: the <span className="font-medium text-foreground">Import</span>{' '}
            page. Drag and drop a CSV export from one of your bank or card accounts, and a module in the backend
            reads it, translates whatever column names and codes that specific institution uses into this app's own
            shared vocabulary, and links the resulting rows to an account.
          </p>
          <p>
            That translation step is the important part. A checking account's CSV, a credit card's CSV, and a
            savings account's CSV rarely use the same column names or the same codes for the same things even within
            one bank — let alone across different banks. So each account type gets its own small, dedicated piece of
            code that knows exactly how to read its particular shape of file and turn it into the app's standard
            format. When you add an account whose CSV shape the app hasn't seen before, the Import page flags it with
            a warning instead of guessing — at that point, a new import module needs to be added in the backend code
            to teach the app that specific format before files from that account can be dropped in directly.
          </p>
          <p>
            You add a new account directly from the Import page. Every account has a <em>kind</em>, which tells the
            rest of the app what it represents:
          </p>
          <AccountKindTable />
          <p>
            <code className="rounded bg-muted px-1 py-0.5 text-xs">external_investment</code> is the one exception to
            everything else on this page — it's a placeholder account that never has transactions of its own imported
            into it. Instead, its value is pulled directly from the Investments side of the app, which is a
            completely separate module tracking brokerage accounts and other volatile assets. This is the one link
            between the two halves of the app, and it only flows in one direction: Net Worth reads a live number from
            Investments; nothing in Investments ever reads accounting data back.
          </p>
        </Section>

        <Section id="ledger" icon={GitBranch} title="The ledger & postings">
          <Definition term="Ledger">
            the complete, append-only record of every transaction ever created from every CSV you've imported.
            Nothing else in the app stores its own separate numbers — every account balance, every net worth figure,
            every income/expense total is recomputed by walking through this record, a process this app calls{' '}
            <span className="font-medium text-foreground">replaying</span> the ledger. If two different charts ever
            disagreed about a total, that would mean one of them forgot to replay the same ledger — it's never a case
            of "which cached number is right," because nothing is cached.
          </Definition>
          <p>
            Once a CSV is standardized, every row in it becomes a <em>transaction</em>: one real economic event, like
            a paycheck landing or a card swipe. A transaction is never stored as a single row, though — it's
            represented as a small group of <em>postings</em> that all share one transaction id.
          </p>
          <Definition term="Posting">
            one row of one transaction: one account, one signed amount, one date. The amount is signed from that
            posting's <em>own</em> account's point of view — positive means money arrived in that account, negative
            means it left. People sometimes call one posting a <span className="font-medium text-foreground">leg</span>{' '}
            of its transaction — "the checking leg" versus "the credit card leg" of the same payment — but a leg isn't
            a different kind of object, it's just a posting looked at as one side of the transaction it belongs to.
          </Definition>
          <p>
            Every transaction always comes from somewhere and goes somewhere, so the postings belonging to one
            transaction always sum to exactly zero (in one currency). Two postings — one negative, one positive — is
            the ordinary case: a simple expense or a simple transfer. But nothing in the schema requires exactly two;
            a transaction can have any number of postings as long as they still sum to zero. That's what makes the{' '}
            <span className="font-medium text-foreground">splitting</span> feature possible, covered further down this
            page.
          </p>
          <p>Here's a real deposit, shown as the two postings it actually becomes once imported:</p>
          <ExampleTable
            caption="One $10,000 incoming wire → one transaction, two postings"
            columns={['posting_id', 'transaction_id', 'account', 'posted_at', 'amount', 'description']}
            rows={[
              ['txn-8f21a3:0', 'txn-8f21a3', 'Meridian Savings', '2026-01-28', '+10,000.00', 'Northgate Trust'],
              ['txn-8f21a3:1', 'txn-8f21a3', 'Uncategorized Income (virtual)', '2026-01-28', '-10,000.00', 'Northgate Trust'],
            ]}
          />
          <p>
            Both rows share one <code className="rounded bg-muted px-1 py-0.5 text-xs">transaction_id</code>, both
            carry the same date and description (that's how an importer builds the pair), and the two amounts cancel
            out. The savings account genuinely received $10,000 — but the <em>other</em> side of that money, whoever
            actually sent it, isn't known yet. That's exactly what the next section covers.
          </p>
          <p>
            Because the ledger is only ever <em>replayed</em>, never edited in place, it can always be thrown away and
            rebuilt: every CSV or statement PDF you've ever uploaded is separately archived, exactly as received,
            forever. If the derived ledger were ever to become corrupted or drift out of sync, the{' '}
            <span className="font-medium text-foreground">Rebuild ledger from raw archives</span> button on the
            Import page recomputes it from scratch from those originals — nothing is ever permanently lost.
          </p>
        </Section>

        <Section id="placeholders" icon={FolderTree} title="Placeholder accounts">
          <p>
            Every posting's counterparty is either one of your own real accounts, or — when the real counterparty
            isn't known yet — one of exactly two virtual placeholder accounts:
          </p>
          <ExampleTable
            caption="The two virtual placeholder accounts"
            columns={['account_id', 'kind', 'display name']}
            rows={[
              ['uncategorized:expense', 'expense_payee', 'Uncategorized Expense'],
              ['uncategorized:income', 'income_source', 'Uncategorized Income'],
            ]}
          />
          <p>
            These two accounts are hidden from the Transactions list — you'll never see "Uncategorized Income" listed
            as if it were one of your accounts. But they matter enormously for every income/expense chart: the pie
            chart, the monthly income-vs-expense chart, and every category total only ever count a posting whose{' '}
            <em>sibling</em> posting (the other side of its transaction) is still sitting on one of these two
            placeholders. The moment a{' '}
            <span className="font-medium text-foreground">transfer rule</span> repoints that placeholder to a real
            account (your credit card, another one of your own accounts), the transaction becomes a transfer between
            two accounts you hold — and every one of those charts correctly stops counting it, since moving your own
            money between your own accounts is neither income nor an expense.
          </p>
        </Section>

        <Section id="rules" icon={ArrowRightLeft} title="Transfer rules">
          <Definition term="Transfer rule">
            a trigger/action pair you define once: "if a posting on this account contains this text in its
            description, repoint its placeholder counterparty to this other account (and optionally set this
            category)." Transfer rules exist specifically to link two of your own accounts together — everything
            that isn't resolved by one stays pointed at one of the two virtual placeholders above.
          </Definition>
          <p>
            (Transfer rules are a different thing from the description-match <em>category patterns</em> covered in
            Automated categorization below — a transfer rule repoints a posting's counterparty automatically, with no
            confirmation step; a category pattern only ever suggests a category, and always needs to be applied and
            validated by hand.)
          </p>
          <p>
            Here's the mechanic that trips people up the most: paying off your own credit card from your own checking
            account isn't imported as one event — it's imported as{' '}
            <em>two separate transactions</em>, because your bank's statement and your card's statement are each
            imported independently and neither one knows the other exists. Each side still has its own unresolved
            placeholder leg:
          </p>
          <ExampleTable
            caption="One real transfer, imported as two transactions (four postings)"
            columns={['posting_id', 'transaction_id', 'account', 'amount', 'description']}
            rows={[
              ['txn-a1:0', 'txn-a1', 'Meridian Checking', '-85.00', 'Payment to Meridian Credit Card ending 4421'],
              ['txn-a1:1', 'txn-a1', 'Uncategorized Expense (virtual)', '+85.00', 'Payment to Meridian Credit Card ending 4421'],
              ['txn-b2:0', 'txn-b2', 'Meridian Credit Card', '+85.00', 'Payment Received – Thank You'],
              ['txn-b2:1', 'txn-b2', 'Uncategorized Income (virtual)', '-85.00', 'Payment Received – Thank You'],
            ]}
          />
          <p>
            A transfer rule only ever fixes <em>one</em> side: it matches on one account's own description and
            repoints <em>that transaction's</em> placeholder leg to whatever counterparty you name. It never
            automatically creates or triggers the mirror rule for the other side. So a pair like this needs two
            transfer rules if you want both transactions fully resolved:
          </p>
          <ul className="ml-4 list-disc space-y-1">
            <li>
              <span className="font-medium text-foreground">Transfer rule 1</span> — on Meridian Checking, if
              description contains "Payment to Meridian Credit Card", repoint to Meridian Credit Card.
            </li>
            <li>
              <span className="font-medium text-foreground">Transfer rule 2</span> — on Meridian Credit Card, if
              description contains "Payment Received", repoint to Meridian Checking.
            </li>
          </ul>
          <p>
            Add only one of the two, and <em>that</em> transaction alone gets reclassified as an internal transfer
            (and disappears from income/expense tracking) — while the other transaction is left exactly as it was,
            still showing up as an ordinary, uncategorized posting. Because of this, the app always proposes both
            directions together as one action when it detects a likely transfer — see the next section.
          </p>
        </Section>

        <Section id="transfer-suggestions" icon={ArrowRightLeft} title="Auto-detected transfer suggestions">
          <p>
            Writing every rule by hand isn't required — the app also scans for pairs of still-unresolved postings
            that look like the same transfer: the same amount, opposite sign, on two different real accounts,
            landing within a configurable number of days of each other (a deposit is checked against postings some
            number of days <em>before</em> it; a payment is checked against postings some number of days{' '}
            <em>after</em> it, since either side of a transfer can be the one that clears first). Matches are shown
            as suggestions on the Accounting page, each with a proposed rule for both directions, fully editable
            before you add either one — the heuristic can be wrong (two unrelated transactions that happen to share
            an amount), so nothing is ever applied automatically.
          </p>
        </Section>

        <Section id="categorizing" icon={Tags} title="Categorizing & tags">
          <p>
            Once a posting's counterparty is resolved to a real account (or intentionally left as real income or a
            real expense against a placeholder), it's ready to categorize.
          </p>
          <Definition term="Category">
            what kind of spend or income a posting represents — Groceries, Salary, Rent. Categories are two levels
            deep at most: a top-level category, and optionally one subcategory beneath it (Food & Drink →
            Groceries). Every category is either an income category or an expense category, matching the sign of the
            postings it's used on.
          </Definition>
          <Definition term="Tag">
            a label that cuts <em>across</em> categories, describing what a posting was part of rather than what kind
            of spend it was. A "Trip to Acadia" tag might sit on postings under Transport, Lodging, and Food & Drink
            all at once — so "how much did the Acadia trip cost, in total" is a normal question regardless of how
            each individual charge was categorized. The same idea applies to a one-off event like a "Christmas 2026"
            tag spanning Gifts, Food & Drink, and Travel.
          </Definition>
        </Section>

        <Section id="automated-categorization" icon={Sparkles} title="Automated categorization">
          <p>
            Categorizing every transaction by hand is the main friction point in using a tool like this, so two
            independent, optional helpers exist — both are suggestion engines only; neither one ever silently
            categorizes anything:
          </p>
          <ul className="ml-4 list-disc space-y-1">
            <li>
              <span className="font-medium text-foreground">AI suggestions</span> — an LLM looks at a posting's
              description alongside examples of postings you've already categorized, and suggests a category and
              subcategory.
            </li>
            <li>
              <span className="font-medium text-foreground">Category patterns</span> — a plain, self-defined rule you
              write yourself: if a posting's description contains this text, suggest this category. It's the
              description-match equivalent of the AI suggestion, with no LLM involved.
            </li>
          </ul>
          <p>
            Both land the exact same way: as a temporary, colored suggestion on the row in Transactions (green for an
            AI suggestion, blue for a category pattern) — nothing is final yet. You click{' '}
            <span className="font-medium text-foreground">Apply</span> on a row (or run bulk suggestions across every
            uncategorized row currently in view) to stage a suggestion, then click{' '}
            <span className="font-medium text-foreground">Validate</span> to actually confirm it. Uncheck a
            suggestion you disagree with before validating, and it reverts to exactly what it was before — nothing
            about a posting changes until that validate step.
          </p>
        </Section>

        <Section id="splitting" icon={Scissors} title="Splitting">
          <p>
            The idea underlying everything above — a transaction is just a set of postings that sum to zero — has one
            more use: a single deposit can be divided into several categorized pieces instead of just two. A plain
            employer deposit of $1,000 is ordinarily two postings (-$1,000 against the virtual income placeholder,
            +$1,000 into your bank account). Splitting lets that same $1,000 arrival become three postings instead —
            still summing to zero — for example -$1,000 against the placeholder, +$700 categorized as Salary, and
            +$300 categorized as a health insurance reimbursement.
          </p>
          <p>
            This is exactly what the paystub import tool on the Import page automates: it reads an uploaded paystub
            PDF, matches its numbers against the real bank deposit(s) that arrived around payday (a single paystub
            can pay out into more than one account at once), and proposes a split for each deposit using whatever
            breakdown the paystub itself lists — separating salary from reimbursements and bonuses automatically,
            for you to review before applying.
          </p>
        </Section>

        <Section id="budgets" icon={Wallet} title="Budgets">
          <p>
            A budget assigns a target number — a "pocket" — to an expense category, so actual spend in that category
            can be measured against it. Budgets can be set two ways: a <em>general</em> budget applies the same target
            to every month alike, while a <em>per-month</em> budget sets a separate target for one specific month.
            Both are just numbers laid over the same categorized postings described above — nothing about a budget
            changes how a transaction is categorized or which charts it appears in.
          </p>
        </Section>

        <Section id="goals" icon={Target} title="Goals">
          <p>
            A goal is somewhere you're deliberately setting money aside — an emergency fund, a vacation, a big
            purchase — tracked separately from ordinary categorized spending. Under the hood, a goal is built from
            just two ideas.
          </p>
          <Definition term="Goal">
            a target: a name, a target amount, an optional target date, and a color. A goal never stores its own
            balance.
          </Definition>
          <Definition term="Contribution">
            one dated, signed entry — money going into a goal (positive) or coming back out of it (negative), on a
            real date. A goal's balance at any point in time is simply the sum of its own contributions up to that
            date, the same "replay, don't store" idea the rest of the app is built on. Contributions are never
            bucketed by month; "this month's contributions" is just a filter applied when displaying them, not a
            separate figure kept somewhere.
          </Definition>
          <p>
            Alongside every goal, the app also tracks{' '}
            <span className="font-medium text-foreground">unallocated money</span> — everything you've earned minus
            everything you've spent, minus whatever you've already put toward any goal. This is never stored as if it
            were its own goal; it's a number computed fresh each time, the leftover after every real goal's
            contributions are subtracted out. It shows up as its own slice in a few charts for convenience, but it has
            no contributions of its own and nothing writes to it directly.
          </p>
          <p>Two optional automations build on top of this:</p>
          <ul className="ml-4 list-disc space-y-1">
            <li>
              <span className="font-medium text-foreground">Recurring additions</span> — an ordered list of rules
              that automatically move unallocated money into goals on a schedule (e.g. the 1st of every month), each
              either a fixed amount or a percentage of whatever's unallocated at the time. They run in priority order,
              so if there isn't enough unallocated money to fund every rule in full, the top-priority one is funded
              first and lower-priority ones get whatever's left over (or nothing). The lowest-priority rule may
              instead be set to take "the remainder" — whatever's left after every rule above it.
            </li>
            <li>
              <span className="font-medium text-foreground">Withdrawal automation</span> — a separate, ordered list
              that only ever triggers when unallocated money drops below zero (for example, after a large expense).
              When that happens, money is pulled back out of goals, in priority order, until unallocated is back to
              zero — never taking any single goal below zero itself. If every goal in the list is exhausted and
              unallocated is still negative, it's simply left negative with a warning shown, rather than the app
              inventing money that isn't there.
            </li>
          </ul>
        </Section>
      </div>
    </div>
  )
}
